# -*- coding: utf-8 -*-
"""main.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1dB_Dwq4_Kp_B_ON1mZaFb72e5-X93DTX
"""

import os
import re
import time
import enum


import cv2 as cv
import numpy as np
import matplotlib.pyplot as plt

import torch
from torch import nn
from torch.optim import Adam
from torchvision import transforms, datasets
from torchvision.utils import make_grid, save_image
from torch.utils.data import Dataset
from torch.utils.data import DataLoader
from torch.utils.tensorboard import SummaryWriter
import gc

import torch
from GPUtil import showUtilization as gpu_usage
from numba import cuda


DRIVE_PATH = os.getcwd()

BINARIES_PATH = os.path.join(DRIVE_PATH, 'models', 'binaries')  
CHECKPOINTS_PATH = os.path.join(DRIVE_PATH, 'models', 'checkpoints')
MODEL_PATH = os.path.join(DRIVE_PATH, 'models', 'binaries', 'NIH_CXR.pth') 
#DATA_DIR_PATH = os.path.join(DRIVE_PATH, 'data_half/images')
DATA_DIR_PATH = "/nfs/ada/joshi/users/anantak1/data/NIH_CXR_data/images"
DEBUG_IMAGERY_PATH = os.path.join(DRIVE_PATH, 'debug_imagery')
GENERATED_IMAGES_PATH = os.path.join(DRIVE_PATH, 'generated_imagery')

IMG_SIZE = 256
BATCH_SIZE = 8

#free_gpu_cache()  

transform = transforms.Compose([
    # you can add other transformations in this list
    transforms.Grayscale(),
    transforms.Resize(IMG_SIZE),
    transforms.ToTensor()
])

img_dataset = datasets.ImageFolder(DATA_DIR_PATH, transform=transform)

img_dataloader = torch.utils.data.DataLoader(img_dataset, batch_size=BATCH_SIZE, drop_last=True, shuffle=True)



# Visualize the data

print(f'Dataset size: {len(img_dataset)} images.')

"""num_imgs_to_visualize = 1  
batch = next(iter(img_dataloader)) 
img_batch = batch[0]  
img_batch_subset = img_batch[:num_imgs_to_visualize] 

print(f'Image shape {img_batch_subset.shape[1:]}') 
grid = make_grid(img_batch_subset, nrow=int(np.sqrt(num_imgs_to_visualize)), normalize=True, pad_value=1.)
grid = np.moveaxis(grid.numpy(), 0, 2)  # from CHW -> HWC format that's what matplotlib expects! Get used to this.
plt.figure(figsize=(6, 6))
plt.title("Samples from the NIH_CXR dataset")
plt.imshow(grid)
plt.show()"""

# Size of the generator's input vector.
LATENT_SPACE_DIM = 100

#free_gpu_cache()  

# This one will produce a batch of those vectors
def get_gaussian_latent_batch(batch_size, device):
    return torch.randn((batch_size, LATENT_SPACE_DIM), device=device)


def vanilla_block(in_feat, out_feat, normalize=True, activation=None):
    layers = [nn.Linear(in_feat, out_feat)]
    if normalize:
        layers.append(nn.BatchNorm1d(out_feat))
    layers.append(nn.LeakyReLU(0.2) if activation is None else activation)
    return layers

class GeneratorNet(torch.nn.Module):
    def __init__(self, img_shape=(IMG_SIZE, IMG_SIZE)):
        super().__init__()
        self.generated_img_shape = img_shape
        num_neurons_per_layer = [LATENT_SPACE_DIM, 512, 1024, 4096, img_shape[0] * img_shape[1]]

        self.net = nn.Sequential(
            *vanilla_block(num_neurons_per_layer[0], num_neurons_per_layer[1]),
            *vanilla_block(num_neurons_per_layer[1], num_neurons_per_layer[2]),
            *vanilla_block(num_neurons_per_layer[2], num_neurons_per_layer[3]),
            *vanilla_block(num_neurons_per_layer[3], num_neurons_per_layer[4], normalize=False, activation=nn.Tanh())
        )

    def forward(self, latent_vector_batch):
        img_batch_flattened = self.net(latent_vector_batch)
        return img_batch_flattened.view(img_batch_flattened.shape[0], 1, *self.generated_img_shape)

class DiscriminatorNet(torch.nn.Module):
    def __init__(self, img_shape=(IMG_SIZE, IMG_SIZE)):
        super().__init__()
        num_neurons_per_layer = [img_shape[0] * img_shape[1], 512, 256, 1]

        # Last layer is Sigmoid function - basically the goal of the discriminator is to output 1.
        # for real images and 0. for fake images and sigmoid is clamped between 0 and 1 so it's perfect.
        self.net = nn.Sequential(
            *vanilla_block(num_neurons_per_layer[0], num_neurons_per_layer[1], normalize=False),
            *vanilla_block(num_neurons_per_layer[1], num_neurons_per_layer[2], normalize=False),
	    *vanilla_block(num_neurons_per_layer[2], num_neurons_per_layer[3], normalize=False, activation=nn.Sigmoid())
        )

    def forward(self, img_batch):
        img_batch_flattened = img_batch.view(img_batch.shape[0], -1)  # flatten from (N,1,H,W) into (N, HxW)
        return self.net(img_batch_flattened)

def get_optimizers(d_net, g_net):
    d_opt = Adam(d_net.parameters(), lr=0.0001, betas=(0.5, 0.999))
    g_opt = Adam(g_net.parameters(), lr=0.0001, betas=(0.5, 0.999))
    return d_opt, g_opt

#free_gpu_cache()  

device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

discriminator_net = DiscriminatorNet().train().to(device)
generator_net = GeneratorNet().train().to(device)

discriminator_opt, generator_opt = get_optimizers(discriminator_net, generator_net)

adversarial_loss = nn.BCELoss()
real_images_gt = torch.ones((BATCH_SIZE, 1), device=device)
fake_images_gt = torch.zeros((BATCH_SIZE, 1), device=device)

checkpoint_freq = 2
console_log_freq = 50

debug_imagery_log_freq = 50

ref_batch_size = 16
ref_noise_batch = get_gaussian_latent_batch(ref_batch_size, device)  # Track G's quality during training on fixed noise vectors
img_cnt = 0

num_epochs = 5

ts = time.time()

def train_GAN():
  for epoch in range(num_epochs):
    for batch_idx, (real_images, _) in enumerate(img_dataloader):
        global img_cnt

        real_images = real_images.to(device)
        
        discriminator_opt.zero_grad()

        real_discriminator_loss = adversarial_loss(discriminator_net(real_images), real_images_gt)

        fake_images = generator_net(get_gaussian_latent_batch(BATCH_SIZE, device))
        fake_images_predictions = discriminator_net(fake_images.detach())
        fake_discriminator_loss = adversarial_loss(fake_images_predictions, fake_images_gt)

        discriminator_loss = real_discriminator_loss + fake_discriminator_loss
        discriminator_loss.backward()
        discriminator_opt.step()


        generator_opt.zero_grad()
        generated_images_predictions = discriminator_net(generator_net(get_gaussian_latent_batch(BATCH_SIZE, device)))
        generator_loss = adversarial_loss(generated_images_predictions, real_images_gt)

        generator_loss.backward()
        generator_opt.step()
        
	# Save intermediate generator images (more convenient like this than through tensorboard)
        if batch_idx % debug_imagery_log_freq == 0:
            with torch.no_grad():
                log_generated_images = generator_net(ref_noise_batch)
                log_generated_images_resized = nn.Upsample(scale_factor=2.5, mode='nearest')(log_generated_images)
                out_path = os.path.join(DEBUG_IMAGERY_PATH, f'{str(img_cnt).zfill(6)}.jpg')
                save_image(log_generated_images_resized, out_path, nrow=int(np.sqrt(ref_batch_size)), normalize=True)
                img_cnt += 1
        
        if batch_idx % console_log_freq == 0:
            prefix = 'GAN training: time elapsed'
            print(
                f'{prefix} = {(time.time() - ts):.2f} [s] | epoch={epoch + 1} | batch= [{batch_idx + 1}/{len(img_dataloader)}]')
            
        # Save generator checkpoint
        if (epoch + 1) % checkpoint_freq == 0 and batch_idx == 0:
            ckpt_model_name = f"vanilla_ckpt_epoch_{epoch + 1}_batch_{batch_idx + 1}.pth"
            torch.save(generator_net.state_dict(), os.path.join(CHECKPOINTS_PATH, ckpt_model_name))

  # Save the latest generator in the binaries directory
  torch.save(generator_net.state_dict(), MODEL_PATH)

train_GAN()

def postprocess_generated_img(generated_img_tensor):
    assert isinstance(generated_img_tensor,
                      torch.Tensor), f'Expected PyTorch tensor but got {type(generated_img_tensor)}.'

    generated_img = np.moveaxis(generated_img_tensor.to('cpu').numpy()[0], 0, 2)

    generated_img = np.repeat(generated_img, 3, axis=2)

    generated_img -= np.min(generated_img)
    generated_img /= np.max(generated_img)

    return generated_img

def generate_from_random_latent_vector(generator):
    with torch.no_grad():  # Tells PyTorch not to compute gradients which would have huge memory footprint

        # Generate a single random (latent) vector
        latent_vector = get_gaussian_latent_batch(1, next(generator.parameters()).device)

        # Post process generator output (as it's in the [-1, 1] range, remember?)
        generated_img = postprocess_generated_img(generator(latent_vector))

    return generated_img

def save_and_maybe_display_image(dump_img, out_res=(256, 256), should_display=False):
    assert isinstance(dump_img, np.ndarray), f'Expected numpy array got {type(dump_img)}.'

    os.makedirs(GENERATED_IMAGES_PATH, exist_ok=True)

    dump_img_name = "new_image.jpg"

    if dump_img.dtype != np.uint8:
        dump_img = (dump_img * 255).astype(np.uint8)

    cv.imwrite(os.path.join(GENERATED_IMAGES_PATH, dump_img_name),
               cv.resize(dump_img[:, :, ::-1], out_res, interpolation=cv.INTER_NEAREST))

    if should_display:
        plt.imshow(dump_img)
        plt.show()

def generate_sample_image():
    assert os.path.exists(MODEL_PATH), f'Could not find the model {MODEL_PATH}. You first need to train your generator.'

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    generator = GeneratorNet().to(device)

    generator.load_state_dict(torch.load(MODEL_PATH))
    generator.eval()

    print('Generating new images!')
    generated_img = generate_from_random_latent_vector(generator)
    save_and_maybe_display_image(generated_img, should_display=True)

generate_sample_image()
