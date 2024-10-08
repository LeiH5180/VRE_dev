import numpy as np
import torch
import torch.nn.functional as F
import torchvision.transforms as TF
import torchvision.datasets as datasets
import kornia
import utils
import os
import random
import math
import time
from arguments import parse_args

places_dataloader = None
places_iter = None

args = parse_args()

global de_num
de_num = args.de_num

def _load_places(batch_size=3000, image_size=84, num_workers=16, use_val=False):
	global places_dataloader, places_iter
	partition = 'val' if use_val else 'train'
	print(f'Loading {partition} partition of places365_standard...')
	for data_dir in utils.load_config('datasets'):
		if os.path.exists(data_dir):
			fp = os.path.join(data_dir, 'places365_standard', partition)
			if not os.path.exists(fp):
				print(f'Warning: path {fp} does not exist, falling back to {data_dir}')
				fp = data_dir
			places_dataloader = torch.utils.data.DataLoader(
				datasets.ImageFolder(fp, TF.Compose([
					TF.RandomResizedCrop(image_size),
					TF.RandomHorizontalFlip(),
					TF.ToTensor()
				])),
				batch_size=batch_size, shuffle=True,
				num_workers=num_workers, pin_memory=True)
			places_iter = iter(places_dataloader)
			break
	if places_iter is None:
		raise FileNotFoundError('failed to find places365 data at any of the specified paths')
	print('Loaded dataset from', data_dir)


def _get_places_batch(batch_size):
	global places_iter
	try:
		imgs, _ = next(places_iter)
		if imgs.size(0) < batch_size:
			places_iter = iter(places_dataloader)
			imgs, _ = next(places_iter)
	except StopIteration:
		places_iter = iter(places_dataloader)
		imgs, _ = next(places_iter)
	return imgs.cuda(de_num)


def random_overlay(x, dataset='places365_standard'):
	"""Randomly overlay an image from Places"""
	global places_iter
	alpha = 0.5

	if dataset == 'places365_standard':
		if places_dataloader is None:
			_load_places(batch_size=x.size(0), image_size=x.size(-1))
		imgs = _get_places_batch(batch_size=x.size(0)).repeat(1, x.size(1)//3, 1, 1)
	else:
		raise NotImplementedError(f'overlay has not been implemented for dataset "{dataset}"')

	return ((1-alpha)*(x/255.) + (alpha)*imgs)*255.

def random_overlay_rand(x, dataset='places365_standard'):
	"""Randomly overlay an image from Places"""
	global places_iter
	alpha = 0.3*torch.rand(x.size(0), 9, 84, 84)+0.35
	alpha = alpha.cuda(de_num)
	if dataset == 'places365_standard':
		if places_dataloader is None:
			_load_places(batch_size=x.size(0), image_size=x.size(-1))
		imgs = _get_places_batch(batch_size=x.size(0)).repeat(1, x.size(1)//3, 1, 1)
	else:
		raise NotImplementedError(f'overlay has not been implemented for dataset "{dataset}"')

	return ((1-alpha)*(x/255.) + (alpha)*imgs)*255.


def random_conv(x):
	"""Applies a random conv2d, deviates slightly from https://arxiv.org/abs/1910.05396"""
	n, c, h, w = x.shape
	for i in range(n):
		weights = torch.randn(3, 3, 3, 3).to(x.device)
		temp_x = x[i:i+1].reshape(-1, 3, h, w)/255.
		temp_x = F.pad(temp_x, pad=[1]*4, mode='replicate')
		out = torch.sigmoid(F.conv2d(temp_x, weights))*255.
		total_out = out if i == 0 else torch.cat([total_out, out], axis=0)
	return total_out.reshape(n, c, h, w)

def random_choose_double(x):
	# start_time = time.time()
	assert isinstance(x, torch.Tensor), 'image input must be tensor'
	n, c, h, w = x.shape
	x_conv = random_conv(x)
	x_over = random_overlay(x)

	mask = (torch.rand(n, c//3, h, w) > 0.5).float().to(x.device)  # 大于0.5的为1，否则为0
	mask_expanded = mask.unsqueeze(2).repeat(1, 1, 3, 1, 1)
	mask_conv = mask_expanded.view(n, c, h, w)
	mask = (torch.rand(n, c//3, h, w) > 0.333).float().to(x.device)
	mask_expanded = mask.unsqueeze(2).repeat(1, 1, 3, 1, 1)
	mask_over = mask_expanded.view(n, c, h, w)

	x_re = (x*mask_conv + x_conv*(1-mask_conv))
	x_re = (x_re*mask_over + x_over*(1-mask_over))

	# time_cost = time.time() - start_time
	# print(f'time cost: {time_cost:.6f}')
	return x_re

def random_choose(x):
	# start_time = time.time()
	assert isinstance(x, torch.Tensor), 'image input must be tensor'
	n, c, h, w = x.shape
	x_over = random_overlay(x)

	mask = (torch.rand(n, c//3, h, w) > 0.5).float().to(x.device)  # 大于0.5的为1，否则为0
	mask_expanded = mask.unsqueeze(2).repeat(1, 1, 3, 1, 1)
	mask_over = mask_expanded.view(n, c, h, w)

	x_re = (x*mask_over + x_over*(1-mask_over))

	# time_cost = time.time() - start_time
	# print(f'time cost: {time_cost:.6f}')
	return x_re

def mask_gen(length, ratio=0.4, n_i=1):
	'''
	ratio: the expected ratio of number of pixels to be masked
	'''
	assert ratio <=1 , 'ratio must not larger than 1'
	if ratio <= 0.5:
		rand_n = random.randint(0,math.floor(length*2*ratio))
	else:
		rand_n = random.randint(math.floor(length*(2*ratio-1)), length)
	mask_out = torch.tensor([])
	for i in range(n_i):
		idx = random.sample(range(length), rand_n)
		mask = torch.ones(length)
		mask[idx] = 0
		mask_out = torch.cat([mask_out, mask.unsqueeze(0).repeat(3, 1)])
	return mask_out



def batch_from_obs(obs, batch_size=32):
	"""Copy a single observation along the batch dimension"""
	if isinstance(obs, torch.Tensor):
		if len(obs.shape)==3:
			obs = obs.unsqueeze(0)
		return obs.repeat(batch_size, 1, 1, 1)

	if len(obs.shape)==3:
		obs = np.expand_dims(obs, axis=0)
	return np.repeat(obs, repeats=batch_size, axis=0)


def prepare_pad_batch(obs, next_obs, action, batch_size=32):
	"""Prepare batch for self-supervised policy adaptation at test-time"""
	batch_obs = batch_from_obs(torch.from_numpy(obs).cuda(de_num), batch_size)
	batch_next_obs = batch_from_obs(torch.from_numpy(next_obs).cuda(de_num), batch_size)
	batch_action = torch.from_numpy(action).cuda(de_num).unsqueeze(0).repeat(batch_size, 1)

	return random_crop_cuda(batch_obs), random_crop_cuda(batch_next_obs), batch_action


def identity(x):
	return x


def random_shift(imgs, pad=4):
	"""Vectorized random shift, imgs: (B,C,H,W), pad: #pixels"""
	_,_,h,w = imgs.shape
	imgs = F.pad(imgs, (pad, pad, pad, pad), mode='replicate')
	return kornia.augmentation.RandomCrop((h, w))(imgs)


def random_crop(x, size=84, w1=None, h1=None, return_w1_h1=False):
	"""Vectorized CUDA implementation of random crop, imgs: (B,C,H,W), size: output size"""
	assert (w1 is None and h1 is None) or (w1 is not None and h1 is not None), \
		'must either specify both w1 and h1 or neither of them'
	assert isinstance(x, torch.Tensor) and x.is_cuda, \
		'input must be CUDA tensor'
	
	n = x.shape[0]
	img_size = x.shape[-1]
	crop_max = img_size - size

	if crop_max <= 0:
		if return_w1_h1:
			return x, None, None
		return x

	x = x.permute(0, 2, 3, 1)

	if w1 is None:
		w1 = torch.LongTensor(n).random_(0, crop_max)
		h1 = torch.LongTensor(n).random_(0, crop_max)

	windows = view_as_windows_cuda(x, (1, size, size, 1))[..., 0,:,:, 0]
	cropped = windows[torch.arange(n), w1, h1]

	if return_w1_h1:
		return cropped, w1, h1

	return cropped


def view_as_windows_cuda(x, window_shape):
	"""PyTorch CUDA-enabled implementation of view_as_windows"""
	assert isinstance(window_shape, tuple) and len(window_shape) == len(x.shape), \
		'window_shape must be a tuple with same number of dimensions as x'
	
	slices = tuple(slice(None, None, st) for st in torch.ones(4).long())
	win_indices_shape = [
		x.size(0),
		x.size(1)-int(window_shape[1]),
		x.size(2)-int(window_shape[2]),
		x.size(3)    
	]

	new_shape = tuple(list(win_indices_shape) + list(window_shape))
	strides = tuple(list(x[slices].stride()) + list(x.stride()))

	return x.as_strided(new_shape, strides)

	