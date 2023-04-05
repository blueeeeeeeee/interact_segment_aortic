import torch
import numpy as np
import os
import cv2
import h5py
import SimpleITK as sitk

from UNet_COPY import *
from interact_dataset import *

def get_network_input(image, seeds, window_transform_flag):
    ele = []
    for i in range(seeds.shape[0]):
        ele.append(image[seeds[i,0], seeds[i,1]])
    ele = np.array(ele)

    image_processed = window_transform(image, max(ele.max() - ele.min() + 2 * np.sqrt(ele.var()), 255), (ele.max() + ele.min()) / 2) if window_transform_flag else image
    image_float = sitk.Cast(sitk.GetImageFromArray(image), sitk.sitkFloat32)
    sobel_op = sitk.SobelEdgeDetectionImageFilter()
    sobel_sitk = sobel_op.Execute(image_float)
    sobel_sitk = sitk.GetArrayFromImage(sobel_sitk)
    sobel_sitk = sobel_sitk - sobel_sitk.min()
    sobel_sitk = sobel_sitk / sobel_sitk.max()

    seeds_image = np.zeros(image.shape)
    for i in range(seeds.shape[0]):
        seeds_image[seeds[i,0], seeds[i,1]] = 1

    return np.stack((image_processed, sobel_sitk, seeds_image))


def get_network_input_all(image, seeds, seeds_image, window_transform_flag, feature_flag):
    ele = []
    for i in range(seeds.shape[0]):
        ele.append(image[seeds[i,0], seeds[i,1]])
    ele = np.array(ele)

    image_processed = window_transform(image, max(ele.max() - ele.min() + 2 * np.sqrt(ele.var()), 255), (ele.max() + ele.min()) / 2) if window_transform_flag else image

    image_float = sitk.Cast(sitk.GetImageFromArray(image), sitk.sitkFloat32)
    sobel_op = sitk.SobelEdgeDetectionImageFilter()
    sobel_sitk = sobel_op.Execute(image_float)
    sobel_sitk = sitk.GetArrayFromImage(sobel_sitk)
    sobel_sitk = sobel_sitk - sobel_sitk.min()
    sobel_sitk = sobel_sitk / sobel_sitk.max()


    return np.stack((image_processed, sobel_sitk, seeds_image)) if feature_flag else np.stack((image_processed, seeds_image))


    
def get_prediction(model, indata):
    prediction = model(indata).cpu().squeeze()
    prediction = torch.sigmoid(prediction)
    # prediction = torch.sigmoid(prediction).detach().numpy()
    prediction = prediction.detach().numpy()
    # prediction = prediction - prediction.min()
    # prediction = prediction / prediction.max()
    prediction = np.where(prediction > 0.5, 1, 0)

    return prediction

def get_prediction_all(model, indata):
    prediction = model(indata).cpu().squeeze()
    prediction = torch.softmax(prediction, dim=0)
    # prediction = torch.sigmoid(prediction).detach().numpy()
    prediction = prediction.detach().numpy()
    # prediction = prediction - prediction.min()
    # prediction = prediction / prediction.max()
    prediction = np.argmax(prediction, axis=0)

    return prediction

def test(image_path, save_path, model_weight_path, window_transform_flag):
    file_image = h5py.File(image_path, 'r')

    image_data = (file_image['image'])[()]
    image_label = (file_image['label'])[()]

    image_data = image_data - image_data.min()
    height, width, depth = image_data.shape

    array_ones = np.ones((height, width))
    array_zeros = np.zeros((height, width))
    array_predict = np.zeros(image_data.shape)
    
    device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")

    model = U_Net()
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()
    


    start_piece = 93 # int(depth / 2)
    start_image = image_data[:,:,start_piece]
    start_label = image_label[:,:,start_piece]
    cur_image = image_data[:,:,start_piece]
    last_image = image_data[:,:,start_piece]

    label_class = int(start_label.max())
    for cur_class in range(label_class, 0, -1):
        last_curkind_label = np.where(start_label == cur_class, array_ones, array_zeros)
        last_image = start_image
        for i in range(start_piece, depth):
            cur_image = image_data[:,:,i]
            flag, seeds = get_right_seeds(last_curkind_label, cur_image, last_image)
            if not flag:
                break
            indata = get_network_input(cur_image, seeds, window_transform_flag)
            indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
            # prediction = (model(indata).cpu().squeeze()).detach().numpy()
            # prediction = prediction - prediction.min()
            # prediction = prediction / prediction.max()
            # prediction = np.where(prediction > 0.5, array_ones, array_zeros)
            prediction = get_prediction(model, indata)
            # print(np.unique(prediction, return_counts = True))
            # print(prediction.shape)
            array_predict[:,:,i] = np.where(prediction == 1, prediction * cur_class, array_predict[:,:,i])
            last_image = image_data[:,:,i]
            last_curkind_label = prediction
            print(f'cur label class: [{cur_class}/{label_class}], cur piece: [{i}/{depth}]')

        last_curkind_label = np.where(start_label == cur_class, array_ones, array_zeros)
        last_image = start_image
        for i in range(start_piece, -1, -1):
            cur_image = image_data[:,:,i]
            flag, seeds = get_right_seeds(last_curkind_label, cur_image, last_image)
            if not flag:
                break
            indata = get_network_input(cur_image, seeds, window_transform_flag)
            indata = torch.from_numpy(indata).unsqueeze(0).to(device=device, dtype=torch.float32)
            # prediction = (model(indata).cpu().squeeze()).detach().numpy()
            # prediction = prediction - prediction.min()
            # prediction = prediction / prediction.max()
            # prediction = np.where(prediction > 0.5, array_ones, array_zeros)
            prediction = get_prediction(model, indata)
            # print(np.unique(prediction, return_counts = True))
            array_predict[:,:,i] = np.where(prediction == 1, prediction * cur_class, array_predict[:,:,i])
            last_image = image_data[:,:,i]
            last_curkind_label = prediction
            print(f'cur label class: [{cur_class}/{label_class}], cur piece: [{i}/{depth}]')

    save2h5(save_path, ['image', 'label', 'prediction'], [image_data, image_label, array_predict])
    

def test_all(image_path, save_path, model_weight_path, window_transform_flag, FLT_flag, sobel_flag, feature_flag, in_channels, out_channels):
    file_image = h5py.File(image_path, 'r')

    image_data = (file_image['image'])[()]
    image_label = (file_image['label'])[()]

    image_data = image_data - image_data.min()
    
    if not FLT_flag:
        image_label = np.where(image_label == 3, 0, image_label)
    image_label = np.uint8(image_label)
    height, width, depth = image_data.shape

    array_predict = np.zeros(image_data.shape)
    
    device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")

    
    model = U_Net(in_channels, out_channels) 
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()
    


    start_piece = 103 # int(depth / 2)
    start_image = image_data[:,:,start_piece]
    start_label = image_label[:,:,start_piece]
    cur_image = image_data[:,:,start_piece]
    last_image = image_data[:,:,start_piece]
    last_label = start_label

    for i in range(start_piece, depth):
        cur_image = image_data[:,:,i]
        flag, seeds, seeds_map = get_right_seeds_all(last_label, cur_image, last_image)
        if not flag:
            break
        indata = get_network_input_all(cur_image, seeds, seeds_map, window_transform_flag, feature_flag)
        if not sobel_flag:
            indata[1,:,:] = array_predict[:,:,i - 1]
        indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
        prediction = get_prediction_all(model, indata)
        prediction = np.uint8(prediction)
        # print(np.unique(prediction, return_counts = True))
        # print(prediction.shape)
        array_predict[:,:,i] = prediction
        last_image = image_data[:,:,i]
        last_label = prediction
        
        print(f'cur piece: [{i}/{depth}]')

    
    last_image = start_image
    last_label = start_label
    for i in range(start_piece - 1, -1, -1):
        cur_image = image_data[:,:,i]
        flag, seeds, seeds_map = get_right_seeds_all(last_label, cur_image, last_image)
        if not flag:
            break
        indata = get_network_input_all(cur_image, seeds, seeds_map, window_transform_flag, feature_flag)
        if not sobel_flag:
            indata[1,:,:] = array_predict[:,:,i + 1]
        indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
        
        prediction = get_prediction_all(model, indata)
        prediction = np.uint8(prediction)
        # print(np.unique(prediction, return_counts = True))
        # print(prediction.shape)
        array_predict[:,:,i] = prediction
        last_image = image_data[:,:,i]
        last_label = prediction
        print(f'cur piece: [{i}/{depth}]')

    save2h5(save_path, ['image', 'label', 'prediction'], [image_data, image_label, array_predict])



if __name__ == '__main__':
    test_all(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_seeds_scribble/validate_2_transform_seeds_scribble_loss_1.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_seeds_scribble/U_Net_transform_seeds_scribble_loss_1.pth', True, False, True, True, 3, 3)
