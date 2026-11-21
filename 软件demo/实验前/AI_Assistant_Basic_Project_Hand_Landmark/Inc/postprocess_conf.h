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

/* Select palm detector postprocess */
// TODO: Define the correct post processing type for palm detection model
#define POSTPROCESS_TYPE POSTPROCESS_MPE_PD_UF

/* I/O configuration */
// TODO: Define the correct width & height of palm detection model
#define AI_PD_MODEL_PP_WIDTH                      (256)
#define AI_PD_MODEL_PP_HEIGHT                     (256)
// TODO: Define the correct total detections before post processing
#define AI_PD_MODEL_PP_TOTAL_DETECTIONS           (10)

#define AI_PD_MODEL_PP_NB_KEYPOINTS               (7)

/* --------  Tuning below can be modified by the application --------- */
#define AI_PD_MODEL_PP_CONF_THRESHOLD              (0.5f)
#define AI_PD_MODEL_PP_IOU_THRESHOLD               (0.4f)
#define AI_PD_MODEL_PP_MAX_BOXES_LIMIT             (20)

#endif      /* __POSTPROCESS_CONF_H__  */

