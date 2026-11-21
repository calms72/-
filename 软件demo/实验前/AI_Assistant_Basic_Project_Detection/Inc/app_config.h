/**
 ******************************************************************************
 * @file    app_config.h
 * @author  GPM Application Team
 *
 ******************************************************************************
 * @attention
 *
 * Copyright (c) 2023 STMicroelectronics.
 * All rights reserved.
 *
 * This software is licensed under terms that can be found in the LICENSE file
 * in the root directory of this software component.
 * If no LICENSE file comes with this software, it is provided AS-IS.
 *
 ******************************************************************************
 */
#ifndef APP_CONFIG
#define APP_CONFIG

#define USE_DCACHE

#include "postprocess_conf.h"

/* Define sensor orientation */
#define CAMERA_FLIP CMW_MIRRORFLIP_NONE

/* Define display size */
#define LCD_BG_WIDTH 480
#define LCD_BG_HEIGHT 480
/* Delay display by DISPLAY_DELAY frame number */
#define DISPLAY_DELAY 1

/* Model Related Info */
// TODO: Define the correct post-processing type
#define POSTPROCESS_TYPE                          POSTPROCESS_OD_YOLO_V8_UI   
// TODO: Define Yolov8 post processing MACRO for Multi-Pose Estimation: Max number of boxes; Confidence threshold; IOU threshold

// TODO: Define the correct width & height for NN input image
#define NN_WIDTH 224
#define NN_HEIGHT 224
// TODO: Define the correct output buffer size for NN
#define NN_BUFFER_OUT_SIZE 5880

#define NN_FORMAT DCMIPP_PIXEL_PACKER_FORMAT_RGB888_YUV444_1
#define NN_BPP 3
#define NB_CLASSES 80   // 例如 COCO 有80类
#define DECLARE_CLASSES_TABLE const char* classes_table[NB_CLASSES] = {\
  "person", "bicycle", "car", "motorcycle", "airplane", "bus", "train", "truck", "boat", \
  "traffic light", "fire hydrant", "stop sign", "parking meter", "bench", "bird", "cat", \
  "dog", "horse", "sheep", "cow", "elephant", "bear", "zebra", "giraffe", "backpack", \
  "umbrella", "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard", "sports ball", \
  "kite", "baseball bat", "baseball glove", "skateboard", "surfboard", "tennis racket", \
  "bottle", "wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple", \
  "sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair", \
  "couch", "potted plant", "bed", "dining table", "toilet", "tv", "laptop", "mouse", \
  "remote", "keyboard", "cell phone", "microwave", "oven", "toaster", "sink", \
  "refrigerator", "book", "clock", "vase", "scissors", "teddy bear", "hair drier", \
  "toothbrush"}

#endif
