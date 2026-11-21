/*---------------------------------------------------------------------------------------------
#  * Copyright (c) 2023 STMicroelectronics.
#  * All rights reserved.
#  *
#  * This software is licensed under terms that can be found in the LICENSE file in
#  * the root directory of this software component.
#  * If no LICENSE file comes with this software, it is provided AS-IS.
#  *--------------------------------------------------------------------------------------------*/

/* ---------------    Generated code    ----------------- */
#ifndef __POSTPROCESS_CONF_H__
#define __POSTPROCESS_CONF_H__

#include "arm_math.h"

/* I/O configuration */
// TODO: Define parameters for Yolov8 pose estimation model: Number of class; Total number of boxes before post processing; Number of Keypoints
#define AI_OBJDETECT_YOLOV8_PP_NB_CLASSES         (80)   
#define AI_OBJDETECT_YOLOV8_PP_TOTAL_BOXES        (10)
#define AI_OBJDETECT_YOLOV8_PP_MAX_BOXES_LIMIT    (100)
#define AI_OBJDETECT_YOLOV8_PP_CONF_THRESHOLD     (0.35f)
#define AI_OBJDETECT_YOLOV8_PP_IOU_THRESHOLD      (0.45f)
#define AI_OBJDETECT_YOLOV8_PP_SCALE              (0.005177631974220276f)   // 输出反量化 scale
#define AI_OBJDETECT_YOLOV8_PP_ZERO_POINT         (-121)                      // 输出反量化 zero point

#endif      /* __POSTPROCESS_CONF_H__  */

