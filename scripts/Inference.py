"""make variations of input image"""

import argparse, os, sys, glob
import PIL
import torch
import numpy as np
import torchvision
from omegaconf import OmegaConf
from PIL import Image
from tqdm import tqdm, trange
from itertools import islice
from einops import rearrange, repeat
from torchvision.utils import make_grid
from torch import autocast
from contextlib import nullcontext
import time
from pytorch_lightning import seed_everything
import cv2
from transformers import CLIPProcessor
import cv2
from insightface.app import FaceAnalysis
from insightface.utils import face_align
import pyiqa
import sys
import os
root_dir = os.path.abspath(os.getcwd())
sys.path.append(root_dir)
from ldm.util import instantiate_from_config
from ldm.models.diffusion.ddim import DDIMSampler
from ldm.models.diffusion.plms import PLMSSampler
import math
import copy
from scripts.wavelet_color_fix import wavelet_reconstruction, adaptive_instance_normalization
import torch.nn.functional as F



def space_timesteps(num_timesteps, section_counts):
	if isinstance(section_counts, str):
		if section_counts.startswith("ddim"):
			desired_count = int(section_counts[len("ddim"):])
			for i in range(1, num_timesteps):
				if len(range(0, num_timesteps, i)) == desired_count:
					return set(range(0, num_timesteps, i))
			raise ValueError(
				f"cannot create exactly {num_timesteps} steps with an integer stride"
			)
		section_counts = [int(x) for x in section_counts.split(",")]   #[250,]
	size_per = num_timesteps // len(section_counts)
	extra = num_timesteps % len(section_counts)
	start_idx = 0
	all_steps = []
	for i, section_count in enumerate(section_counts):
		size = size_per + (1 if i < extra else 0)
		if size < section_count:
			raise ValueError(
				f"cannot divide section of {size} steps into {section_count}"
			)
		if section_count <= 1:
			frac_stride = 1
		else:
			frac_stride = (size - 1) / (section_count - 1)
		cur_idx = 0.0
		taken_steps = []
		for _ in range(section_count):
			taken_steps.append(start_idx + round(cur_idx))
			cur_idx += frac_stride
		all_steps += taken_steps
		start_idx += size
	return set(all_steps)

def chunk(it, size):
	it = iter(it)
	return iter(lambda: tuple(islice(it, size)), ())

def load_model_from_config(config, ckpt, verbose=False):
	print(f"Loading model from {ckpt}")
	pl_sd = torch.load(ckpt, map_location="cpu")
	if "global_step" in pl_sd:
		print(f"Global Step: {pl_sd['global_step']}")
	sd = pl_sd["state_dict"]
	model = instantiate_from_config(config.model)

	IPAsd = sd

	m, u = model.load_state_dict(IPAsd, strict=False)
	if len(m) > 0 and verbose:
		print("missing keys:")
		print(m)
	if len(u) > 0 and verbose:
		print("unexpected keys:")
		print(u)

	model.cuda()
	model.eval()
	return model

def load_img(path):
	image = Image.open(path).convert("RGB")
	w, h = image.size
	print(f"loaded input image of size ({w}, {h}) from {path}")
	w, h = map(lambda x: x - x % 32, (w, h))  # resize to integer multiple of 32
	image = image.resize((w, h), resample=PIL.Image.LANCZOS)
	image = np.array(image).astype(np.float32) / 255.0
	image = image[None].transpose(0, 3, 1, 2)
	image = torch.from_numpy(image)
	return 2.*image - 1.

def main():
	parser = argparse.ArgumentParser()

	parser.add_argument(
		"--LQpath",
		type=str,
		nargs="?",
		help="path to the input LQ image",
		default="./",
	)
	parser.add_argument(
		"--Refpath",
		type=str,
		nargs="?",
		help="path to the ref image",
		default="./",
	)
	parser.add_argument(
		"--Outputpath",
		type=str,
		nargs="?",
		help="path to the output image",
		default="./",
	)
	parser.add_argument(
		"--ddim_steps",
		type=int,
		default=50,
		help="number of ddim sampling steps",
	)
	parser.add_argument(
		"--config",
		type=str,
		default="configs/v15/v15-RestorerID.yaml",
		help="path to config which constructs model",
	)
	parser.add_argument(
		"--ckpt",
		type=str,
		default="ckpt/RestorerIDFull.ckpt",
		help="path to checkpoint of model",
	)
	parser.add_argument(
		"--seed",
		type=int,
		default=42,
		help="the seed (for reproducible sampling)",
	)
	parser.add_argument(
		"--precision",
		type=str,
		help="evaluate at this precision",
		choices=["full", "autocast"],
		default="autocast"
	)

	parser.add_argument(
		"--colorfix_type",
		type=str,
		default="wavelet",
		help="Color fix type to adjust the color of HR result according to LR input: adain (used in paper); wavelet; nofix",
	)

	opt = parser.parse_args()
	device = torch.device("cuda") if torch.cuda.is_available() else torch.device("cpu")

	print('>>>>>>>>>>color correction>>>>>>>>>>>')
	if opt.colorfix_type == 'adain':
		print('Use adain color correction')
	elif opt.colorfix_type == 'wavelet':
		print('Use wavelet color correction')
	else:
		print('No color correction')
	print('>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>')


	seed_everything(opt.seed)

	app = FaceAnalysis(name="buffalo_l", providers=['CUDAExecutionProvider'])
	app.prepare(ctx_id=0, det_size=(512,512))

	config = OmegaConf.load(f"{opt.config}")
	model = load_model_from_config(config, f"{opt.ckpt}")
	model = model.to(device)

	model.register_schedule(given_betas=None, beta_schedule="linear", timesteps=1000,
						linear_start=0.00085, linear_end=0.0120, cosine_s=8e-3)
	model.num_timesteps = 1000

	sampler = DDIMSampler(model)
	ddim_eta = 1.0
	ddim_num_steps = opt.ddim_steps
	sampler.make_schedule(ddim_num_steps=ddim_num_steps, ddim_eta=ddim_eta, verbose=False)

	ddim_timesteps = set(space_timesteps(1000, [ddim_num_steps]))
	ddim_timesteps = list(ddim_timesteps)
	ddim_timesteps.sort()

	use_timesteps = set(space_timesteps(1000, [1000]))
	last_alpha_cumprod = 1.0
	new_betas = []
	timestep_map = []
	for i, alpha_cumprod in enumerate(model.alphas_cumprod):
		if i in use_timesteps:
			new_betas.append(1 - alpha_cumprod / last_alpha_cumprod)
			last_alpha_cumprod = alpha_cumprod
			timestep_map.append(i)
	new_betas = [beta.data.cpu().numpy() for beta in new_betas]
	model.register_schedule(given_betas=np.array(new_betas), timesteps=len(new_betas))
	model.num_timesteps = 1000
	model.ori_timesteps = list(use_timesteps)
	model.ori_timesteps.sort()
	model = model.to(device)

	musiq_metric = pyiqa.create_metric('musiq', device=device)
	precision_scope = autocast if opt.precision == "autocast" else nullcontext

	with torch.no_grad():
		with precision_scope("cuda"):
			with model.ema_scope():
				lq_img = load_img(opt.LQpath).to(device)
				lq_latent_generator = model.encode_first_stage(lq_img)
				lq_latent = model.get_first_stage_encoding(lq_latent_generator)

				ref_img = cv2.imread(opt.Refpath)
				faces = app.get(ref_img)
				faceid_embeds = torch.from_numpy(faces[0].normed_embedding).unsqueeze(0)
				ref_image = face_align.norm_crop(ref_img, landmark=faces[0].kps, image_size=224) # you can also segment the face

				ref_c,uncon_ref_c = model.Adapter(faceid_embeds, ref_image)
				positive_text_c = ["best quality, high quality"]
				positive_semantic_c = model.cond_stage_model(positive_text_c)
				negative_text_c= ["cartoon, 3d, paint, monochrome, lowres, bad anatomy, worst quality, low quality"]
				negative_semantic_c = model.cond_stage_model(negative_text_c)

				positive_cond = torch.cat([positive_semantic_c,ref_c],dim=1)
				negative_cond = torch.cat([negative_semantic_c,uncon_ref_c],dim=1)


				musiq = musiq_metric((lq_img+1.0)/2)
				ipscale = math.exp(0.1*(9.5-musiq))
				print(ipscale)
				x_T = None
				
				samples, _ = sampler.ddim_sampling_sr_t(cond=positive_cond,
											struct_cond=lq_latent,
											ipscale = ipscale,
											shape=lq_latent.shape,
											unconditional_conditioning=negative_cond,
											unconditional_guidance_scale=4,
											timesteps=np.array(ddim_timesteps),
											x_T=x_T)
				x_samples = model.decode_first_stage(samples)

				if opt.colorfix_type == 'adain':
					x_samples = adaptive_instance_normalization(x_samples, lq_img)
				elif opt.colorfix_type == 'wavelet':
					x_samples = wavelet_reconstruction(x_samples, lq_img)
				x_samples = torch.clamp((x_samples + 1.0) / 2.0, min=0.0, max=1.0)
				
				for index in range(1):
					x_sample = 255. * rearrange(x_samples[index].cpu().numpy(), 'c h w -> h w c')
					filename = opt.LQpath.split('/')[-1]
					os.makedirs(opt.Outputpath,exist_ok=True)
					Image.fromarray(x_sample.astype(np.uint8)).save(os.path.join(opt.Outputpath,filename))
					

	print(f"Your samples are ready and waiting for you here: \n{opt.Outputpath} \n"
		  f" \nEnjoy.")


if __name__ == "__main__":
	main()
