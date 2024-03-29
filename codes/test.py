import torch
import numpy as np
import os
import cv2
import h5py
import SimpleITK as sitk
import nibabel as nib

from UNet import *
from interact_dataset import *
from train import accuracy_all_numpy, dice_3d
import matplotlib.pyplot as plt
from scipy.spatial.distance import directed_hausdorff
from medpy.metric import binary
import timeit

"""
can use the software to test acc
"""

def get_network_input(image, seeds, seeds_image, window_transform_flag):
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

    # seeds_image = np.zeros(image.shape)
    # for i in range(seeds.shape[0]):
    #     seeds_image[seeds[i,0], seeds[i,1]] = 1

    return np.stack((image_processed, sobel_sitk, get_curclass_label(seeds_image, 0), get_curclass_label(seeds_image, 1)))


def get_network_input_all(image, seeds, seeds_image, window_transform_flag):
    ele = []
    for i in range(seeds.shape[0]):
        ele.append(image[seeds[i,0], seeds[i,1]])
    ele = np.array(ele)

    image_processed = window_transform(image, max(ele.max() - ele.min() + 2 * np.sqrt(ele.var()), 255), (ele.max() + ele.min()) / 2) if window_transform_flag else image
    # image_processed = window_transform(image, ele.max() - ele.min(), (ele.max() + ele.min()) / 2) if window_transform_flag else image
    # print("window transform")
    image_float = sitk.Cast(sitk.GetImageFromArray(image), sitk.sitkFloat32)
    # print("sobel1")
    sobel_op = sitk.SobelEdgeDetectionImageFilter()
    # print("sobel2")
    sobel_sitk = sobel_op.Execute(image_float)
    # print("sobel3")
    sobel_sitk = sitk.GetArrayFromImage(sobel_sitk)
    # print("sobel4")
    sobel_sitk = sobel_sitk - sobel_sitk.min()
    sobel_sitk = sobel_sitk / sobel_sitk.max()
    # print("sobel5")
    # plt.imshow(sobel_sitk, cmap='gray')
    # plt.axis('off')
    # plt.show()


    return np.stack((image_processed, sobel_sitk, get_curclass_label(seeds_image, 0), get_curclass_label(seeds_image, 1), get_curclass_label(seeds_image, 2)))


    
def get_prediction(model, indata):
    prediction = model(indata).cpu().squeeze()
    prediction = torch.sigmoid(prediction)
    uncertainty =  (-prediction * torch.log(prediction   + 1e-16)).cpu().detach().numpy()
    # prediction = torch.sigmoid(prediction).detach().numpy()
    prediction = prediction.detach().numpy()
    # prediction = prediction - prediction.min()
    # prediction = prediction / prediction.max()
    prediction = np.where(prediction > 0.5, 1, 0)
    prediction_mask = prediction > 0

    uncertainty_value = (np.sum(uncertainty[prediction_mask]) / np.sum(prediction_mask) if prediction_mask.any() else 0)

    return np.uint8(prediction), uncertainty_value




def get_prediction_all(model, indata, uncertainty_flag = True):
    """
    除了不确定性 还需要考虑 
    scribble loss
    同一连通区域不应该有多种label
    """
    prediction = model(indata).cpu().squeeze()
    prediction = torch.softmax(prediction, dim=0)
    uncertainty =  -torch.sum(prediction * torch.log(prediction   + 1e-16), dim=0).cpu().detach().numpy()
    # print(uncertainty.shape)
    # prediction = torch.sigmoid(prediction).detach().numpy()
    prediction = prediction.detach().numpy()
    # prediction = prediction - prediction.min()
    # prediction = prediction / prediction.max()
    prediction = np.uint8(np.argmax(prediction, axis=0))
    prediction_mask = prediction > 0

    uncertainty_value = (np.sum(uncertainty[prediction_mask]) / np.sum(prediction_mask) if prediction_mask.any() else 0) if uncertainty_flag else 0

    return prediction, uncertainty_value

def test_region(image_path, save_path, model_weight_path, window_transform_flag):
    file_image = h5py.File(image_path, 'r')

    image_data = (file_image['image'])[()]
    image_label = (file_image['label'])[()]

    image_data = image_data - image_data.min()
    image_label = np.uint8(image_label)
    height, width, depth = image_data.shape

    # array_ones = np.ones((height, width))
    # array_zeros = np.zeros((height, width))
    array_predict = np.zeros(image_data.shape)
    
    device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")

    model = U_Net()
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()
    
    for cur_piece in range(depth):
        if image_label[:,:,cur_piece].max() == 0:
            continue
        cur_image = image_data[:,:,cur_piece]
        cur_label = image_label[:,:,cur_piece]
        
        cur_kindregion = 0
        for cur_class in range(1, cur_label.max() + 1):
            cur_curclass_label = np.where(cur_label == cur_class, 1, 0)
            cur_connected_num, cur_connected_labels = cv2.connectedComponents(np.uint8(cur_curclass_label))
            cur_connected_labels = np.uint8(cur_connected_labels)

            for cur_region in range(1, cur_connected_num):
                cur_kindregion = cur_kindregion + 1
                cur_curkind_label = np.where(cur_connected_labels == cur_region, 1, 0)
                flag, seeds = get_right_seeds(cur_curkind_label, cur_image, cur_image, 6)
                if not flag:
                    continue
                indata = get_network_input(cur_image, seeds, window_transform_flag)
                indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
                prediction = get_prediction(model, indata)
                array_predict[:,:,cur_piece] = np.where(prediction == 1, prediction * cur_kindregion, array_predict[:,:,cur_piece])
                print(f'cur piece: [{cur_piece}/{depth}], cur class: [{cur_class} / {cur_label.max()}] cur region: [{cur_region}/{cur_connected_num - 1}], ')

    save2h5(save_path, ['image', 'label', 'prediction'], [image_data, image_label, array_predict])


    # label_class = int(start_label.max())
    # for cur_class in range(label_class, 0, -1):
    #     last_curkind_label = np.where(start_label == cur_class, array_ones, array_zeros)
    #     last_image = start_image
    #     for i in range(start_piece, depth):
    #         cur_image = image_data[:,:,i]
    #         flag, seeds = get_right_seeds(last_curkind_label, cur_image, last_image)
    #         if not flag:
    #             break
    #         indata = get_network_input(cur_image, seeds, window_transform_flag)
    #         indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
    #         # prediction = (model(indata).cpu().squeeze()).detach().numpy()
    #         # prediction = prediction - prediction.min()
    #         # prediction = prediction / prediction.max()
    #         # prediction = np.where(prediction > 0.5, array_ones, array_zeros)
    #         prediction = get_prediction(model, indata)
    #         # print(np.unique(prediction, return_counts = True))
    #         # print(prediction.shape)
    #         array_predict[:,:,i] = np.where(prediction == 1, prediction * cur_class, array_predict[:,:,i])
    #         last_image = image_data[:,:,i]
    #         last_curkind_label = prediction
    #         print(f'cur label class: [{cur_class}/{label_class}], cur piece: [{i}/{depth}]')

    #     last_curkind_label = np.where(start_label == cur_class, array_ones, array_zeros)
    #     last_image = start_image
    #     for i in range(start_piece, -1, -1):
    #         cur_image = image_data[:,:,i]
    #         flag, seeds = get_right_seeds(last_curkind_label, cur_image, last_image)
    #         if not flag:
    #             break
    #         indata = get_network_input(cur_image, seeds, window_transform_flag)
    #         indata = torch.from_numpy(indata).unsqueeze(0).to(device=device, dtype=torch.float32)
    #         # prediction = (model(indata).cpu().squeeze()).detach().numpy()
    #         # prediction = prediction - prediction.min()
    #         # prediction = prediction / prediction.max()
    #         # prediction = np.where(prediction > 0.5, array_ones, array_zeros)
    #         prediction = get_prediction(model, indata)
    #         # print(np.unique(prediction, return_counts = True))
    #         array_predict[:,:,i] = np.where(prediction == 1, prediction * cur_class, array_predict[:,:,i])
    #         last_image = image_data[:,:,i]
    #         last_curkind_label = prediction
    #         print(f'cur label class: [{cur_class}/{label_class}], cur piece: [{i}/{depth}]')
    

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
    

    # change start_piece to get better performance
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
        prediction,_ = get_prediction_all(model, indata)
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
        
        prediction,_ = get_prediction_all(model, indata)
        prediction = np.uint8(prediction)
        # print(np.unique(prediction, return_counts = True))
        # print(prediction.shape)
        array_predict[:,:,i] = prediction
        last_image = image_data[:,:,i]
        last_label = prediction
        print(f'cur piece: [{i}/{depth}]')

    save2h5(save_path, ['image', 'label', 'prediction'], [image_data, image_label, array_predict])


def get_prediction_all_bidirectional(last_label, cur_image, last_image, window_transform_flag, start_flag, device, model, seeds_case, clean_region_flag = False):
    flag, seeds, seeds_map = get_right_seeds_all(last_label, cur_image, last_image, seeds_case=seeds_case, clean_region_flag=clean_region_flag)
    # plt.imshow(seeds_map, cmap='gray')
    # plt.axis('off')
    # plt.show()
    # print("seeds")
    if not flag:
        return False, None, None
    if start_flag:
        seeds_map = get_start_label_cut(seeds_map)
    indata = get_network_input_all(cur_image, seeds, seeds_map, window_transform_flag)
    # print("input")
    indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
    prediction,_ = get_prediction_all(model, indata)
    # print("prediction")
    prediction = np.uint8(prediction)

    return True, prediction, seeds_map

def get_prediction_all_bidirectional_brats(last_label, cur_image, last_image, window_transform_flag, start_flag, device, model, seeds_case, clean_region_flag = False):
    flag, seeds, seeds_map = get_right_seeds_all(last_label, cur_image, last_image, seeds_case=seeds_case, clean_region_flag=clean_region_flag)
    # plt.imshow(seeds_map, cmap='gray')
    # plt.axis('off')
    # plt.show()
    # print("seeds")
    if not flag:
        return False, None, None
    if start_flag:
        seeds_map = get_start_label_cut(seeds_map)
    indata = get_network_input(cur_image, seeds, seeds_map, window_transform_flag)
    # print("input")
    indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
    prediction,_ = get_prediction(model, indata)
    # print("prediction")
    prediction = np.uint8(prediction)

    return True, prediction, seeds_map


def get_prediction_all_bidirectional_mask(last_label, cur_image, last_image, window_transform_flag, feature_flag, sobel_flag, array_predict, nostart_flag, device, model, seeds_case, clean_region_flag = False):
    flag, seeds, seeds_map = get_right_seeds_all(last_label, cur_image, last_image, seeds_case=seeds_case, clean_region_flag=clean_region_flag)
    # plt.imshow(seeds_map, cmap='gray')
    # plt.axis('off')
    # plt.show()
    # print("seeds")
    if not flag:
        return False, None, None
    indata = get_network_input_all(cur_image, seeds, seeds_map, window_transform_flag)
    # print("input")
    if not sobel_flag:
        if nostart_flag:
            indata[1,:,:] = array_predict[:,:,last_label]
        else:
            indata[1,:,:] = np.zeros(last_label.shape)
    indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
    prediction,_ = get_prediction_all(model, indata)
    seeds_map = np.uint8(seeds_map)
    # print("prediction")
    prediction = np.where(seeds_map == 1, 1, prediction)
    prediction = np.where(seeds_map == 2, 2, prediction)
    prediction = np.uint8(prediction)

    return True, prediction, seeds_map


def test_all_bidirectional(image_path, save_path, model_weight_path, window_transform_flag, FLT_flag, sobel_flag, feature_flag, in_channels, out_channels, dice_coeff_thred, seeds_case, clean_region_flag = False):
    """
    img_7 for test bidirectionally
    """
    file_image = h5py.File(image_path, 'r')

    image_data = (file_image['image'])[()]
    image_label = (file_image['label'])[()]

    image_data = image_data - image_data.min()
    
    image_label = np.uint8(image_label)
    height, width, depth = image_data.shape

    array_predict = np.zeros(image_data.shape)
    
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    
    model = U_Net(in_channels, out_channels) 
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()
    

    # change start_piece to get better performance
    start_piece = 42 # int(depth / 2)
    start_image = image_data[:,:,start_piece]
    start_label = image_label[:,:,start_piece]
    cur_image = image_data[:,:,start_piece]
    last_image = image_data[:,:,start_piece]
    last_label = start_label

    for i in range(start_piece, depth):
        cur_image = image_data[:,:,i]
        flag, prediction,_ = get_prediction_all_bidirectional(last_label, cur_image, last_image, window_transform_flag, feature_flag, sobel_flag, array_predict, i - start_piece, device, model, seeds_case, clean_region_flag=clean_region_flag)
        if not flag:
            break
        # print(np.unique(prediction, return_counts = True))
        # print(prediction.shape)
        array_predict[:,:,i] = prediction
        if prediction.max() < 0.5:
            break
        cur_piece = i
        cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
        while cur_piece > 0 and cur_coeff  < dice_coeff_thred:
            roll_flag, roll_prediction,_ = get_prediction_all_bidirectional(array_predict[:,:,cur_piece], image_data[:,:,cur_piece-1], image_data[:,:,cur_piece], window_transform_flag, feature_flag, sobel_flag, array_predict, 1, device, model, seeds_case, clean_region_flag=clean_region_flag)
            if not roll_flag:
                break
            if accuracy_all_numpy(array_predict[:,:,cur_piece - 1], roll_prediction) < 0.98:
                array_predict[:,:,cur_piece - 1] = roll_prediction
            else:
                break
            if roll_prediction.max() < 0.5:
                break
            cur_piece = cur_piece - 1
            cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
        last_image = image_data[:,:,i]
        last_label = prediction
        
        print(f'cur piece: [{i}/{depth}]')

    
    # last_image = start_image
    # last_label = start_label
    # for i in range(start_piece - 1, -1, -1):
    #     cur_image = image_data[:,:,i]
    #     flag, seeds, seeds_map = get_right_seeds_all(last_label, cur_image, last_image)
    #     if not flag:
    #         break
    #     indata = get_network_input_all(cur_image, seeds, seeds_map, window_transform_flag, feature_flag)
    #     if not sobel_flag:
    #         indata[1,:,:] = array_predict[:,:,i + 1]
    #     indata = torch.from_numpy(indata).unsqueeze(0).to(device=device,dtype=torch.float32)
        
    #     prediction = get_prediction_all(model, indata)
    #     prediction = np.uint8(prediction)
    #     # print(np.unique(prediction, return_counts = True))
    #     # print(prediction.shape)
    #     array_predict[:,:,i] = prediction
    #     last_image = image_data[:,:,i]
    #     last_label = prediction
    #     print(f'cur piece: [{i}/{depth}]')

    save2h5(save_path, ['image', 'label', 'prediction'], [image_data, image_label, array_predict])



def test_all_bidirectional_mask(image_path, save_path, model_weight_path, window_transform_flag, FLT_flag, sobel_flag, feature_flag, in_channels, out_channels, dice_coeff_thred, seeds_case, clean_region_flag = False):
    """
    img_7 for test bidirectionally
    """
    file_image = h5py.File(image_path, 'r')

    image_data = (file_image['image'])[()]
    image_label = (file_image['label'])[()]

    image_data = image_data - image_data.min()
    
    image_label = np.uint8(image_label)
    height, width, depth = image_data.shape

    array_predict = np.zeros(image_data.shape)
    
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

    
    model = U_Net(in_channels, out_channels) 
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()
    

    # change start_piece to get better performance
    start_piece = 42 # int(depth / 2)
    start_image = image_data[:,:,start_piece]
    start_label = image_label[:,:,start_piece]
    cur_image = image_data[:,:,start_piece]
    last_image = image_data[:,:,start_piece]
    last_label = start_label

    for i in range(start_piece, depth):
        cur_image = image_data[:,:,i]
        flag, prediction,_ = get_prediction_all_bidirectional_mask(last_label, cur_image, last_image, window_transform_flag, feature_flag, sobel_flag, array_predict, i - start_piece, device, model, seeds_case, clean_region_flag=clean_region_flag)
        if not flag:
            break
        # print(np.unique(prediction, return_counts = True))
        # print(prediction.shape)
        array_predict[:,:,i] = prediction
        if prediction.max() < 0.5:
            break
        cur_piece = i
        cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
        while cur_piece > 0 and cur_coeff  < dice_coeff_thred:
            roll_flag, roll_prediction,_ = get_prediction_all_bidirectional_mask(array_predict[:,:,cur_piece], image_data[:,:,cur_piece-1], image_data[:,:,cur_piece], window_transform_flag, feature_flag, sobel_flag, array_predict, 1, device, model, seeds_case, clean_region_flag=clean_region_flag)
            if not roll_flag:
                break
            if accuracy_all_numpy(array_predict[:,:,cur_piece - 1], roll_prediction) < 0.98:
                array_predict[:,:,cur_piece - 1] = roll_prediction
            else:
                break
            if roll_prediction.max() < 0.5:
                break
            cur_piece = cur_piece - 1
            cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
        last_image = image_data[:,:,i]
        last_label = prediction
        
        print(f'cur piece: [{i}/{depth}]')


    save2h5(save_path, ['image', 'label', 'prediction'], [image_data, image_label, array_predict])


def crop_label(prediction, label):
    height, width, depth = label.shape
    start_index = 0
    end_index = depth
    for i in range(depth):
        if prediction[:,:,i].max() > 0.5 or label[:,:,i].max() > 0.5:
            start_index = i
            break

    for i in range(depth - 1, -1, -1):
        if prediction[:,:,i].max() > 0.5 or label[:,:,i].max() > 0.5:
            end_index = i
            break


    return prediction[:,:,start_index:end_index+1], label[:,:,start_index:end_index+1]


def cal_image_acc_experiment(array_predict_ori_0, image_label_ori_0, log = None, file_name = None):
    array_predict_ori, image_label_ori = crop_label(array_predict_ori_0, image_label_ori_0)
    array_predict_tl = np.bool_(np.where(array_predict_ori == 1, 1, 0))
    image_label_tl = np.bool_(np.where(image_label_ori == 1, 1, 0))
    array_predict_fl = np.bool_(np.where(array_predict_ori == 2, 1, 0))
    image_label_fl = np.bool_(np.where(image_label_ori == 2, 1, 0))
    array_predict = np.bool_(np.where(array_predict_ori > 0, 1, 0))
    image_label = np.bool_(np.where(image_label_ori > 0, 1, 0))
    acc_tl = 0.0
    acc_fl = 0.0
    acc = 0.0
    acc_ori = 0.0
    hd_tl = 0.0
    hd_fl = 0.0
    hd_all = 0.0
    hd_ori = 0.0

    # for d in range(depth):
    #     tmp_acc_tl = accuracy_all_numpy(array_predict_tl[:,:,d], image_label_tl[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc_tl += tmp_acc_tl
    #     hd_tl += max(directed_hausdorff(array_predict_tl[:,:,d], image_label_tl[:,:,d])[0], directed_hausdorff(image_label_tl[:,:,d], array_predict_tl[:,:,d])[0])


    # for d in range(depth):
    #     tmp_acc_fl = accuracy_all_numpy(array_predict_fl[:,:,d], image_label_fl[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc_fl += tmp_acc_fl
    #     hd_fl += max(directed_hausdorff(array_predict_fl[:,:,d], image_label_fl[:,:,d])[0], directed_hausdorff(image_label_fl[:,:,d], array_predict_fl[:,:,d])[0])

    # for d in range(depth):
    #     tmp_acc = accuracy_all_numpy(array_predict[:,:,d], image_label[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc += tmp_acc
    #     hd_all += max(directed_hausdorff(array_predict[:,:,d], image_label[:,:,d])[0], directed_hausdorff(image_label[:,:,d], array_predict[:,:,d])[0])

    # for d in range(depth):
    #     tmp_acc_ori = accuracy_all_numpy(array_predict_ori[:,:,d], image_label_ori[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc_ori += tmp_acc_ori
    #     hd_ori += max(directed_hausdorff(array_predict_ori[:,:,d], image_label_ori[:,:,d])[0], directed_hausdorff(image_label_ori[:,:,d], array_predict_ori[:,:,d])[0])

    dc1,dc2,dc3,hd1,hd2,hd3 = binary.dc(array_predict_tl, image_label_tl), binary.dc(array_predict_fl, image_label_fl) , binary.dc(array_predict, image_label), binary.hd(array_predict_tl, image_label_tl, voxelspacing = 0.25), binary.hd(array_predict_fl, image_label_fl, voxelspacing = 0.25), binary.hd(array_predict, image_label, voxelspacing = 0.25)
    print('file: %s, TL acc: %.5f, FL acc: %.5f, acc: %.5f, hd tl: %.5f, hd fl: %.5f, hd: %.5f' % (file_name, dc1,dc2,dc3,hd1,hd2,hd3))
    # log.write('file: %s, TL acc: %.5f, FL acc: %.5f, acc: %.5f, hd tl: %.5f, hd fl: %.5f, hd: %.5f\n' % (file_name, dc1,dc2,dc3,hd1,hd2,hd3))
    
    return dc1,dc2,dc3,hd1,hd2,hd3



def cal_image_acc_experiment_brats(array_predict_ori, image_label_ori, log, file_name):
    # array_predict_ori = np.uint8(array_predict_ori)
    # image_label_ori = np.uint8(image_label_ori)
    height, width, depth = array_predict_ori.shape
    # array_predict_tl = np.bool_(np.where(array_predict_ori == 1, 1, 0))
    # image_label_tl = np.bool_(np.where(image_label_ori == 1, 1, 0))
    # array_predict_fl = np.bool_(np.where(array_predict_ori == 2, 1, 0))
    # image_label_fl = np.bool_(np.where(image_label_ori == 2, 1, 0))
    # array_predict = np.bool_(np.where(array_predict_ori > 0, 1, 0))
    # image_label = np.bool_(np.where(image_label_ori > 0, 1, 0))
    # acc_tl = 0.0
    # acc_fl = 0.0
    # acc = 0.0
    acc_ori = 0.0
    # hd_tl = 0.0
    # hd_fl = 0.0
    # hd_all = 0.0
    hd_ori = 0.0

    # for d in range(depth):
    #     tmp_acc_tl = accuracy_all_numpy(array_predict_tl[:,:,d], image_label_tl[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc_tl += tmp_acc_tl
    #     hd_tl += max(directed_hausdorff(array_predict_tl[:,:,d], image_label_tl[:,:,d])[0], directed_hausdorff(image_label_tl[:,:,d], array_predict_tl[:,:,d])[0])


    # for d in range(depth):
    #     tmp_acc_fl = accuracy_all_numpy(array_predict_fl[:,:,d], image_label_fl[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc_fl += tmp_acc_fl
    #     hd_fl += max(directed_hausdorff(array_predict_fl[:,:,d], image_label_fl[:,:,d])[0], directed_hausdorff(image_label_fl[:,:,d], array_predict_fl[:,:,d])[0])

    # for d in range(depth):
    #     tmp_acc = accuracy_all_numpy(array_predict[:,:,d], image_label[:,:,d])
    #     # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
    #     acc += tmp_acc
    #     hd_all += max(directed_hausdorff(array_predict[:,:,d], image_label[:,:,d])[0], directed_hausdorff(image_label[:,:,d], array_predict[:,:,d])[0])

    for d in range(depth):
        if array_predict_ori[:,:,d].max() > 0.5 or image_label_ori[:,:,d].max() > 0.5:
            tmp_acc_ori = accuracy_all_numpy(array_predict_ori[:,:,d], image_label_ori[:,:,d])
            # print(f'current file: {file_name}, current piece: {d}/{depth}, acc: {tmp_acc}')
            acc_ori += tmp_acc_ori
            hd_ori += max(directed_hausdorff(array_predict_ori[:,:,d], image_label_ori[:,:,d])[0], directed_hausdorff(image_label_ori[:,:,d], array_predict_ori[:,:,d])[0])

    a = binary.assd(array_predict_ori, image_label_ori) if array_predict_ori.max() > 0.5 and image_label_ori.max() > 0.5 else 0.0
    print('file: %s, depth: %d, TC acc: %.5f, 3D Dice: %.5f, hd TC: %.5f, assd: %.5f' % (file_name, depth, acc_ori / depth, dice_3d(array_predict_ori, image_label_ori), hd_ori, a))
    log.write('file: %s, depth: %d, TC acc: %.5f, 3D Dice: %.5f, hd TC: %.5f, assd: %.5f\n' % (file_name, depth, acc_ori / depth, dice_3d(array_predict_ori, image_label_ori), hd_ori, a))

    return dice_3d(array_predict_ori, image_label_ori), hd_ori, a



def generate_circle_mask(img_height,img_width,radius,center_x,center_y):
 
    y,x=np.ogrid[0:img_height,0:img_width]
 
    # circle mask
 
    mask = (x-center_x)**2+(y-center_y)**2<=radius**2
 
    return mask

def get_start_label_cut(label):
    label = np.uint8(label)
    new_label = np.zeros(label.shape, dtype=np.uint8)
    for i in range(1, label.max()+1):
        cur_label = np.uint8(np.where(label == i, 1, 0))
        if cur_label.max() < 0.5:
            continue
        # 读取图片，转灰度
        coords = np.zeros((0,2), int)
        _, cur_labels = cv2.connectedComponents(cur_label)
        block_num = cur_labels.max()
        for cur_block in range(block_num, 0, -1):
            cur_region_label = np.where(cur_labels > cur_block-0.5, 1, 0)
            cur_labels[cur_labels > cur_block-0.5] = 0

            cur_region_label = np.uint8(cur_region_label)
            coord = np.argwhere(cur_region_label > 0)
            num,_ = coord.shape
            quit_num = int(0.15 * num)

            coord = coord[quit_num:num - quit_num, :]
            coords = np.concatenate((coords, coord), axis=0)
        coords = np.unique(coords, axis=0)
        for c in range(coords.shape[0]):
            new_label[coords[c,0], coords[c,1]] = i

    return new_label


def get_start_label_circle(label):
    """去掉label的特征 得到最大内接圆"""
    label = np.uint8(label)
    new_label = np.zeros(label.shape, dtype=np.uint8)
    for i in range(1, label.max()+1):
        cur_label = np.uint8(np.where(label == i, 1, 0))
        if cur_label.max() < 0.5:
            continue
        # 读取图片，转灰度
        _, cur_labels = cv2.connectedComponents(cur_label)
        block_num = cur_labels.max()
        for cur_block in range(block_num, 0, -1):
            cur_region_label = np.where(cur_labels > cur_block-0.5, 1, 0)
            cur_labels[cur_labels > cur_block-0.5] = 0

            mask_gray = np.uint8(cur_region_label)
            
            # 识别轮廓
            contours, _ = cv2.findContours(mask_gray, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
            
            # 计算到轮廓的距离
            raw_dist = np.empty(mask_gray.shape, dtype=np.float32)
            for i in range(mask_gray.shape[0]):
                for j in range(mask_gray.shape[1]):
                    raw_dist[i, j] = cv2.pointPolygonTest(contours[0], (j, i), True)
            
            # 获取最大值即内接圆半径，中心点坐标
            minVal, maxVal, _, maxDistPt = cv2.minMaxLoc(raw_dist)
            minVal = abs(minVal)
            maxVal = int(abs(maxVal))
            if maxVal > 1:
                maxVal -= 1
            
            # 画出最大内接圆
            mask = generate_circle_mask(label.shape[0], label.shape[1], maxVal, maxDistPt[0], maxDistPt[1])
            new_label = np.where(mask, i, new_label)

    return new_label
    


def test_experiment(image_path, log_path, model_weight_path, seeds_case = 0, window_transform_flag = True, sobel_flag = True, feature_flag = True, in_channels = 5, out_channels = 3, dice_coeff_thred = 0.75, clean_region_flag = False):
    """
    img_7 for test bidirectionally
    """
    log = open(log_path, "a+", buffering=1)
    tl_d = []
    fl_d = []
    aorta_d = []
    tl_h = []
    fl_h = []
    aorta_h = []
    device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")

        
    model = U_Net(in_channels, out_channels) 
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()

    for file_name in open(image_path, 'r'):
        file_name = file_name.replace("\n", "")
        file_image = h5py.File(file_name, 'r')

        image_data = (file_image['image'])[()]
        image_label = (file_image['label'])[()]

        image_data = image_data - image_data.min()
        
        image_label = np.uint8(image_label)
        height, width, depth = image_data.shape
        print("current file: ", file_name)
        array_predict = np.zeros(image_data.shape, dtype=np.uint8)

        # change start_piece to get better performance
        start_piece = int(depth / 4)
        
        start_label = image_label[:,:,start_piece]
        while start_label.max() < 0.5:
            start_piece += 1
            start_label = image_label[:,:,start_piece]
        cur_image = image_data[:,:,start_piece]
        last_image = image_data[:,:,start_piece]
        last_label = start_label

        for i in range(start_piece, depth):
            cur_image = image_data[:,:,i]
            # print("before get prediction")
            flag, prediction,_ = get_prediction_all_bidirectional(last_label, cur_image, last_image, window_transform_flag, i == start_piece, device, model, seeds_case, clean_region_flag=clean_region_flag)
            # print("after get prediction")
            if not flag:
                break
            # print(np.unique(prediction, return_counts = True))
            # print(prediction.shape)
            array_predict[:,:,i] = prediction
            # tmp_acc = accuracy_all_numpy(prediction, image_label[:,:,i])
            # print(f'current file: {file_name}, current piece: {i}/{depth}, acc: {tmp_acc}')
            # acc += tmp_acc
            # acc_num += 1
            if prediction.max() < 0.5:
                break
            cur_piece = i
            cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
            # print(i)
            while cur_piece > 0 and cur_coeff  < dice_coeff_thred:
                # print(cur_piece)
                # print("start roll prediction")
                roll_flag, roll_prediction,_ = get_prediction_all_bidirectional(array_predict[:,:,cur_piece], image_data[:,:,cur_piece-1], image_data[:,:,cur_piece], window_transform_flag, 0, device, model, seeds_case, clean_region_flag=clean_region_flag)
                # print("finish roll prediction")
                if not roll_flag:
                    break
                if accuracy_all_numpy(array_predict[:,:,cur_piece - 1], roll_prediction) < 0.98:
                    array_predict[:,:,cur_piece - 1] = roll_prediction
                    # tmp_acc = accuracy_all_numpy(roll_prediction, image_label[:,:,cur_piece - 1])
                    # print(f'current file: {file_name}, current piece: {cur_piece - 1}/{depth}, acc: {tmp_acc}')
                    # acc += tmp_acc
                    # acc_num += 1
                else:
                    break
                if roll_prediction.max() < 0.5:
                    break
                cur_piece = cur_piece - 1
                if cur_piece > 0:
                    cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
                else:
                    break
            last_image = image_data[:,:,i]
            last_label = prediction
            # print("finish segment cur piece ", i)
            
        tl1, fl1, aorta1, tl2, fl2, aorta2 = cal_image_acc_experiment(array_predict_ori_0=array_predict, image_label_ori_0=image_label, log=log, file_name=file_name)
        tl_d.append(tl1)
        tl_h.append(tl2)
        fl_d.append(fl1)
        fl_h.append(fl2)
        aorta_d.append(aorta1)
        aorta_h.append(aorta2)

    tl_d = np.array(tl_d)
    fl_d = np.array(fl_d)
    aorta_d = np.array(aorta_d)
    tl_h = np.array(tl_h)
    fl_h = np.array(fl_h)
    aorta_h = np.array(aorta_h)
    print('dice: tl: %.2f[%.2f], fl: %.2f[%.2f], aorta: %.2f[%.2f]' % (tl_d.mean(), np.sqrt(tl_d.var()), fl_d.mean(), np.sqrt(fl_d.var()), aorta_d.mean(), np.sqrt(aorta_d.var())))
    print('hauf: tl: %.2f[%.2f], fl: %.2f[%.2f], aorta: %.2f[%.2f]' % (tl_h.mean(), np.sqrt(tl_h.var()), fl_h.mean(), np.sqrt(fl_h.var()), aorta_h.mean(), np.sqrt(aorta_h.var())))

        
    log.close()


def test_experiment_brats(image_path, log_path, model_weight_path, pre_path = "/mnt/xuxin/BraTS/", seeds_case = 0, window_transform_flag = True, sobel_flag = True, feature_flag = True, in_channels = 4, out_channels = 1, dice_coeff_thred = 0.75, clean_region_flag = False):
    """
    img_7 for test bidirectionally
    """
    log = open(log_path, "a+", buffering=1)
    tc_d = []
    tc_h = []
    tc_a = []
    run_time = []
    device = torch.device("cuda:2" if torch.cuda.is_available() else "cpu")
    print("device: ", device)
    
    model = U_Net(in_channels, out_channels) 
    # model_weight_path = r'../training_files/two_class/train5_validate2/U_Net_1.pth'
    model.load_state_dict(torch.load(model_weight_path, map_location=device))
    model.to(device)
    model.eval()

    for file_folder in open(image_path, 'r'):
        file_folder = file_folder.replace("\n", "")
        file_name_image = pre_path + file_folder + "/" + file_folder + "_t1ce.nii.gz"
        file_name_label = pre_path + file_folder + "/" + file_folder + "_seg.nii.gz"


        image_obj = nib.load(file_name_image)
        label_obj = nib.load(file_name_label)
        image_data = image_obj.get_fdata()
        image_label = label_obj.get_fdata()
        # 让image data的值大于等于0
        image_data = image_data - image_data.min()
        image_label = np.where(image_label > 1.5, 0, image_label)
        image_label = np.uint8(image_label)
        print("current file: ", file_folder)
        height, width, depth = image_data.shape

        array_predict = np.zeros(image_data.shape, dtype=np.uint8)
        
        # change start_piece to get better performance
        start_pos = 80
        start_piece = start_pos
        
        start_label = image_label[:,:,start_piece]
        while start_label.max() < 0.5:
            start_piece += 10
            if start_piece >= depth:
                start_piece = int(start_pos / 2)
                start_pos = start_piece
                if start_pos == 0:
                    print("ERROR! NO START LABEL!")
                    break
                print("no label to start! set start piece to: ", start_piece)

            start_label = image_label[:,:,start_piece]
        cur_image = image_data[:,:,start_piece]
        last_image = image_data[:,:,start_piece]
        last_label = start_label

        start_time = timeit.default_timer()

        for i in range(start_piece, depth):
            cur_image = image_data[:,:,i]
            flag, prediction,_ = get_prediction_all_bidirectional_brats(last_label, cur_image, last_image, window_transform_flag, 0, device, model, seeds_case, clean_region_flag=clean_region_flag)
            if not flag:
                break
            # print(np.unique(prediction, return_counts = True))
            # print(prediction.shape)
            array_predict[:,:,i] = prediction
            # tmp_acc = accuracy_all_numpy(prediction, image_label[:,:,i])
            # print(f'current file: {file_name}, current piece: {i}/{depth}, acc: {tmp_acc}')
            # acc += tmp_acc
            # acc_num += 1
            if prediction.max() < 0.5:
                break
            cur_piece = i
            cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
            while cur_piece > 0 and cur_coeff  < dice_coeff_thred:
                #print(cur_piece)
                roll_flag, roll_prediction,_ = get_prediction_all_bidirectional_brats(array_predict[:,:,cur_piece], image_data[:,:,cur_piece-1], image_data[:,:,cur_piece], window_transform_flag, 0, device, model, seeds_case, clean_region_flag=clean_region_flag)
                if not roll_flag:
                    break
                if accuracy_all_numpy(array_predict[:,:,cur_piece - 1], roll_prediction) < 0.98:
                    array_predict[:,:,cur_piece - 1] = roll_prediction
                    # tmp_acc = accuracy_all_numpy(roll_prediction, image_label[:,:,cur_piece - 1])
                    # print(f'current file: {file_name}, current piece: {cur_piece - 1}/{depth}, acc: {tmp_acc}')
                    # acc += tmp_acc
                    # acc_num += 1
                else:
                    break
                if roll_prediction.max() < 0.5:
                    break
                cur_piece = cur_piece - 1
                cur_coeff = accuracy_all_numpy(array_predict[:,:,cur_piece-1], array_predict[:,:,cur_piece])
            last_image = image_data[:,:,i]
            last_label = prediction

        end_time = timeit.default_timer()
        print('Running time: %s Seconds'%(end_time - start_time))
        dice, hd, a = cal_image_acc_experiment_brats(array_predict_ori=array_predict, image_label_ori=image_label, log=log, file_name=file_folder)
        if dice > 0.5:
            tc_d.append(dice)
            tc_h.append(hd)
            tc_a.append(a)
            run_time.append(end_time - start_time)
            if len(tc_d) == 45:
                break
    
    if len(tc_d) < 45:
        print('ERROR! LARGER TEST DATASET!')
        return 
    tc_d = np.array(tc_d)
    tc_h = np.array(tc_h)
    run_time = np.array(run_time)
    tc_a = np.array(tc_a)
    print('dice: %.3f [ %.3f ]' % (tc_d.mean(), np.sqrt(tc_d.var())))
    print('hd: %.1f [ %.1f ]' % (tc_h.mean(), np.sqrt(tc_h.var())))
    print('assd: %.2f [ %.2f ]' % (tc_a.mean(), np.sqrt(tc_a.var())))
    print('running time: %.1f [ %.1f ]' % (run_time.mean(), np.sqrt(run_time.var())))

        
    log.close()
    
def test_ITK_SNAP(itk_path, label_path):
    """
    itk_path: contains test folders
    h5_path: contains ground truth in .nii.gz
    """
    tl_d = []
    fl_d = []
    aorta_d = []
    tl_h = []
    fl_h = []
    aorta_h = []
    all_test_folder = os.listdir(itk_path)
    print('there are %d test files' % (len(all_test_folder)))
    for test_file in all_test_folder:
        test_file = test_file.rstrip('\n')
        
        # get itk result
        itk_res1_path = os.path.join(itk_path, test_file, '1.nii.gz')
        itk_res2_path = os.path.join(itk_path, test_file, '2.nii.gz')
        res1_obj = nib.load(itk_res1_path)
        res2_obj = nib.load(itk_res2_path)
        itk_res1 = np.uint8(res1_obj.get_fdata())
        itk_res2 = np.uint8(res2_obj.get_fdata())
        
        itk_res = itk_res1 + itk_res2
        itk_res[itk_res > 2.5] = 0
        # print('finish getting itk result')
        # get ground truth
        gt_path = os.path.join(label_path, str(test_file) + '_label.nii.gz')
        gt_obj = nib.load(gt_path)
        gt_res = np.uint8(gt_obj.get_fdata())
        # print('finish getting gt')
        # evaluation
        tl1, fl1, aorta1, tl2, fl2, aorta2 = cal_image_acc_experiment(array_predict_ori_0=itk_res, image_label_ori_0=gt_res, file_name = test_file)
        #print('dice: tl: %.2f, fl: %.2f, aorta: %.2f' % (tl1, fl1, aorta1))
        #print('hauf: tl: %.2f, fl: %.2f, aorta: %.2f' % (tl2, fl2, aorta2))
        
        tl_d.append(tl1)
        tl_h.append(tl2)
        fl_d.append(fl1)
        fl_h.append(fl2)
        aorta_d.append(aorta1)
        aorta_h.append(aorta2)
        
    tl_d = np.array(tl_d)
    fl_d = np.array(fl_d)
    aorta_d = np.array(aorta_d)
    tl_h = np.array(tl_h)
    fl_h = np.array(fl_h)
    aorta_h = np.array(aorta_h)    
    print('dice: tl: %.2f[%.2f], fl: %.2f[%.2f], aorta: %.2f[%.2f]' % (tl_d.mean(), np.sqrt(tl_d.var()), fl_d.mean(), np.sqrt(fl_d.var()), aorta_d.mean(), np.sqrt(aorta_d.var())))
    print('hauf: tl: %.2f[%.2f], fl: %.2f[%.2f], aorta: %.2f[%.2f]' % (tl_h.mean(), np.sqrt(tl_h.var()), fl_h.mean(), np.sqrt(fl_h.var()), aorta_h.mean(), np.sqrt(aorta_h.var())))

        
    


if __name__ == '__main__':
    print('start!')
    test_ITK_SNAP(itk_path = '/data/xuxin/ITK-SNAP_Results/', label_path = '/data/luwenjing/dataset/ImageTBAD/')
    # test_all_bidirectional(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/validate_2_transform_sobel_scribble_loss_20_0.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/U_Net_transform_sobel_scribble_loss_20.pth', True, False, True, True, 5, 3, 0.75, 0)
    # test_all_bidirectional(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/validate_2_transform_sobel_scribble_loss_20_6.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/U_Net_transform_sobel_scribble_loss_20.pth', True, False, True, True, 5, 3, 0.75, 6)
    # test_all_bidirectional(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/validate_2_transform_sobel_scribble_acc_20_0.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/U_Net_transform_sobel_scribble_acc_20.pth', True, False, True, True, 5, 3, 0.75, 0)
    # test_all_bidirectional(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/validate_2_transform_sobel_scribble_acc_20_6.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/bothkinds_masks/transform_sobel_scribble/U_Net_transform_sobel_scribble_acc_20.pth', True, False, True, True, 5, 3, 0.75, 6)
    # test_region(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/transform_sobel_scribble/validate_2_region_transform_sobel_scribble_loss_6.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/transform_sobel_scribble/U_Net_region_transform_sobel_scribble_loss_5.pth', True)
    # test_region(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/notransform_sobel_scribble/validate_2_region_notransform_sobel_scribble_loss_5.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/notransform_sobel_scribble/U_Net_region_notransform_sobel_scribble_loss_5.pth', False)
    # test_region(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/transform_sobel_scribble/validate_2_region_transform_sobel_scribble_loss_4.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/transform_sobel_scribble/U_Net_region_transform_sobel_scribble_loss_4.pth', True)
    # test_region(r'/data/xuxin/ImageTBAD_processed/two_class/2.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/transform_sobel_scribble/validate_2_region_transform_sobel_scribble_loss_3.h5', r'/data/xuxin/ImageTBAD_processed/training_files/two_class/connected_region/transform_sobel_scribble/U_Net_region_transform_sobel_scribble_loss_3.pth', True)
    # test_experiment(image_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/test.txt',log_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/AD_1/test_log_rotate_flip_dice_loss_2.txt',model_weight_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/AD_1/UNet_rotate_flip_dice_loss_2.pth')
    # test_experiment(image_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/test.txt',log_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/AD_1/test_log_rotate_flip_dice_acc_2.txt',model_weight_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/AD_1/UNet_rotate_flip_dice_acc_2.pth')
    # test_experiment(image_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/AD_1/test.txt',log_path=r'/data/xuxin/ImageTBAD_processed/training_files/experiment/datalist/AD_1/test_final
