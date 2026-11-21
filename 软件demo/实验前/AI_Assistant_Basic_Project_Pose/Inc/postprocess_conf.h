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
#define AI_MPE_YOLOV8_PP_NB_CLASSES               (1)
#define AI_MPE_YOLOV8_PP_TOTAL_BOXES              (10)
#define AI_POSE_PP_POSE_KEYPOINTS_NB              (17)

#endif      /* __POSTPROCESS_CONF_H__  */

