"""
数据格式记录：
image_src(h5文件)：height，width，depth的三维数组，取值范围[0,1]
每次选取特定depth，则处理二维矩阵，height——行数，width——列数——直接视作2D图像

anotation_output(用来表示标注数据)，depth，height，width，red，green，blue三通道（因为QImage是RGB三通道显示的, opencv是BGR三通道显示的）

"""

import os
from collections import OrderedDict
from os.path import join as opj
from turtle import left, shape
from typing import Any
from xml.sax.handler import DTDHandler

import cv2
import matplotlib.pyplot as plt
import maxflow
import numpy as np
import torch
from PIL import Image
from scipy import ndimage
from scipy.ndimage import zoom
from skimage import color, measure
import h5py
from ..UNet_COPY import *
from ..interact_dataset import *
from ..train import accuracy_all_numpy
from ..test import get_prediction_all_bidirectional


class InteractImage(object):
    def __init__(self, image_path):
        """
        add_seed的坐标对应方式与原始图像，也就是h5文件一致
        """
        self.TL_seeds = [] # height, width, depth
        self.FL_seeds = [] 
        self.background_seed = [] # height, width, depth
        file = h5py.File(image_path, 'r')

        self.image = (file['image'])[()]
        self.height, self.width, self.depth = self.image.shape
        self.depth_current = self.depth // 2
        self.prediction = np.zeros((self.depth, self.height, self.width, 3), dtype=np.uint8)

        self.dice_coeff_thred = 0.75

    def set_depth(self, depth):
        self.depth_current = depth

    def gray2BGRImage(self, gray_image):
        gray_image = cv2.normalize(gray_image, None, 0, 255, cv2.NORM_MINMAX)
        # gray_image = (gray_image * 255).astype(np.uint8)
        return cv2.cvtColor(gray_image, cv2.COLOR_GRAY2BGR)

    def getImage2show(self):
        return cv2.addWeighted(self.gray2BGRImage(self.image[:, :, self.depth_current]), 0.9, self.prediction[self.depth_current], 0.7, 0.7)
    
    def init_segment(self, model, device):
        window_transform_flag = True
        feature_flag = True
        sobel_flag = True

        start_image = self.image[:,:,self.depth_current]
        start_label = self.image[:,:,self.depth_current]
        cur_image = self.image[:,:,self.depth_current]
        last_image = self.image[:,:,self.depth_current]
        last_label = start_label

        for i in range(self.depth_current, self.depth):
            cur_image = self.image[:,:,i]
            flag, prediction = get_prediction_all_bidirectional(last_label, cur_image, last_image, window_transform_flag, feature_flag, sobel_flag, self.prediction, i - self.depth_current, device, model)
            if not flag:
                break
            # print(np.unique(prediction, return_counts = True))
            # print(prediction.shape)
            self.prediction[:,:,i] = prediction
            if prediction.max() < 0.5:
                break
            cur_piece = i
            cur_coeff = accuracy_all_numpy(self.prediction[:,:,cur_piece-1], self.prediction[:,:,cur_piece])
            while cur_piece > 0 and cur_coeff  < self.dice_coeff_thred:
                roll_flag, roll_prediction = get_prediction_all_bidirectional(self.prediction[:,:,cur_piece], self.image[:,:,cur_piece-1], self.image[:,:,cur_piece], window_transform_flag, feature_flag, sobel_flag, self.prediction, 1, device, model)
                if not roll_flag:
                    break
                if accuracy_all_numpy(self.prediction[:,:,cur_piece - 1], roll_prediction) < 0.98:
                    self.prediction[:,:,cur_piece - 1] = roll_prediction
                else:
                    break
                if roll_prediction.max() < 0.5:
                    break
                cur_piece = cur_piece - 1
                cur_coeff = accuracy_all_numpy(self.prediction[:,:,cur_piece-1], self.prediction[:,:,cur_piece])
            last_image = self.image[:,:,i]
            last_label = prediction

        
    def Clear(self):
        self.prediction = np.zeros((self.depth, self.height, self.width, 3), dtype=np.uint8)
        self.TL_seeds = [] # height, width, depth
        self.FL_seeds = [] 
        self.background_seed = [] # height, width, depth

    def savePrediction(self, save_path):
        save2h5(save_path, ['image', 'prediction'], [self.image, self.prediction])
