import os
import os.path
import re
import shutil
from enum import Enum
from glob import glob
from typing import NamedTuple, Callable
from collections import OrderedDict
from endaaman import Commander
from endaaman.torch import calc_mean_and_std, pil_to_tensor, tensor_to_pil

import torch
import numpy as np
from tqdm import tqdm
import pandas as pd
from torch.nn import functional as F
from torch.utils.data import Dataset
from PIL import Image, ImageOps, ImageFile
from PIL.Image import Image as ImageType
from sklearn.model_selection import train_test_split
import albumentations as A
from albumentations.pytorch.transforms import ToTensorV2
from albumentations.core.transforms_interface import ImageOnlyTransform
from albumentations.augmentations.crops.functional import center_crop

ImageFile.LOAD_TRUNCATED_IMAGES = True
Image.MAX_IMAGE_PIXELS = 1000000000


DiagToNum = OrderedDict((
    ('L', 0),
    ('M', 1),
    ('G', 2),
    ('A', 3),
    ('O', 4),
))

NumToDiag = list(DiagToNum.keys())

Map5to3 = {
    'L': 'L',
    'M': 'M',
    'G': 'G',
    'A': 'G',
    'O': 'G',
}

# MEAN = [0.8032, 0.5991, 0.8318]
# STD = [0.1203, 0.1435, 0.0829]
# MEAN = np.array([216, 172, 212]) / 255
# STD = np.array([34, 61, 30]) / 255
MEAN = [0.807, 0.611, 0.832]
STD = [0.123, 0.147, 0.087]


class Item(NamedTuple):
    path: str
    diag: str
    image: ImageType
    test: bool


class BTDataset(Dataset):
    def __init__(self,
                 # data spec
                 target='train', merge_G=False, base_dir='data/images',
                 # train-test spec
                 test_ratio=0.25, seed=42, scale=1,
                 # image spec
                 crop_size=768, size=768, aug_mode='same', normalize=True
                 ):
        self.target = target
        self.merge_G = merge_G
        self.base_dir = base_dir

        self.test_ratio = test_ratio
        self.seed = seed
        self.scale = scale

        self.size = size
        self.crop_size = crop_size
        self.aug_mode = aug_mode
        self.normalize = normalize

        self.num_classes = 3 if merge_G else 5

        augs = {}

        augs['train'] = [
            A.RandomCrop(width=crop_size, height=crop_size),
            A.Resize(width=size, height=size),
            A.RandomRotate90(p=1),
            A.HorizontalFlip(p=0.5),
            A.GaussNoise(p=0.2),
            A.OneOf([
                A.MotionBlur(p=0.2),
                A.MedianBlur(blur_limit=3, p=0.1),
                A.Blur(blur_limit=3, p=0.1),
            ], p=0.2),
            A.ShiftScaleRotate(shift_limit=0.0625, scale_limit=0.2, rotate_limit=5, p=0.5),
            A.OneOf([
                A.CLAHE(clip_limit=2),
                A.Emboss(),
                A.RandomBrightnessContrast(),
            ], p=0.3),
            A.HueSaturationValue(p=0.3),
        ]
        augs['test'] = [
            A.CenterCrop(width=size, height=size),
        ]
        augs['all'] = augs['test']

        # select aug
        if aug_mode == 'same':
            aug = augs[target]
        elif aug_mode == 'none':
            aug = []
        else:
            aug = augs[aug_mode]

        if normalize:
            aug += [A.Normalize(mean=MEAN, std=STD)]
        aug += [ToTensorV2()]

        self.albu = A.Compose(aug)
        self.load_data()

    def load_data(self):
        # df_all = pd.read_csv('data/labels.csv')
        data = []
        for diag in NumToDiag:
            for path in glob(os.path.join(self.base_dir, diag, '*.jpg')):
                # merge A and O to G
                diag = Map5to3[diag] if self.merge_G else diag
                data.append({
                    'path': path,
                    'diag': diag,
                })

        df_all = pd.DataFrame(data)
        df_train, df_test = train_test_split(df_all, test_size=self.test_ratio, stratify=df_all.diag, random_state=self.seed)

        if self.target == 'train':
            dfs = [(df_train, False)]
        elif self.target == 'test':
            dfs = [(df_test, True)]
        elif self.target == 'all':
            dfs = [(df_train, False), (df_test, True)]
        else:
            raise ValueError(f'invalid target: {self.target}')

        self.items = []
        for (df, t) in dfs:
            for idx, row in df.iterrows():
                self.items.append(Item(
                    path=row.path,
                    diag=row.diag,
                    image=Image.open(row.path),
                    test=t,
                ))

    def __len__(self):
        l = len(self.items)
        if self.target == 'train':
            return l * self.scale
        return l

    def __getitem__(self, idx):
        item = self.items[idx % len(self.items)]
        x = self.albu(image=np.array(item.image))['image']
        y = torch.tensor(DiagToNum[item.diag])
        # y = F.one_hot(torch.tensor(DiagToNum[item.diag]))
        return x, y


class CMD(Commander):
    def arg_common(self, parser):
        parser.add_argument('--merge', '-m', action='store_true')
        parser.add_argument('--target', '-t', default='all', choices=['all', 'train', 'test'])
        parser.add_argument('--aug', '-a', default='same', choices=['same', 'train', 'test'])
        parser.add_argument('--size', '-s', type=int, default=512)
        parser.add_argument('--csize', '-c', type=int, default=768)

    def pre_common(self):
        self.ds = BTDataset(
            target=self.args.target,
            merge_G=self.args.merge,
            aug_mode=self.args.aug,
            crop_size=self.args.size,
            size=self.args.size,
            normalize=self.args.function != 'samples',
        )

    def arg_samples(self, parser):
        parser.add_argument('--dest', '-d', default='tmp/samples')

    def run_samples(self):
        t = self.args.target
        d = os.path.join(self.a.dest, t)
        os.makedirs(d, exist_ok=True)
        total = len(self.ds)
        for i, (x, y) in tqdm(enumerate(self.ds), total=total):
            if i > total:
                break
            img = tensor_to_pil(x)
            img.save(f'{d}/{i}_{NumToDiag[int(y)]}.jpg')

    def arg_balance(self, parser):
        pass

    def run_balance(self):
        data = []
        ii = []
        for diag in NumToDiag:
            ii.append(diag)
            data.append({
                'pixels': 0,
                'images': 0,
                'mean': 0,
            })
        df = pd.DataFrame(data, index=ii)

        for item in self.ds.items:
            p = item.image.width * item.image.height
            df.loc[item.diag, 'pixels'] += p/1000/1000
            df.loc[item.diag, 'images'] += 1

        for diag in NumToDiag:
            m = df.loc[diag, 'pixels'] / df.loc[diag, 'images']
            df.loc[diag, 'mean'] = m
        self.df = df
        print(df)
        chan = '3' if self.a.merge else '5'
        df.to_csv(f'out/balance_{chan}.csv')

    def run_t(self):
        for (x, y) in self.ds:
            self.x = x
            self.y = y
            print(y, x.shape)
            self.i = tensor_to_pil(x)
            break

if __name__ == '__main__':
    cmd = CMD(options={'no_pre_common': ['split']})
    cmd.run()
