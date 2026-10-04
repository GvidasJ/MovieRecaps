The face and speaker models of `match_cuts/people.py` (`--premiere`: the person speaking is always in the picture)
and `match_cuts/faces.py`:

* `face_detection_yunet_2023mar.onnx` -- YuNet (Wu, Peng, Yu et al. 2023), OpenCV's FaceDetectorYN model, copied
  unchanged from opencv_zoo (`models/face_detection_yunet/`); MIT licence, `YUNET_LICENSE`. It replaced OpenCV's
  Haar cascades.
* `light_asd_talkset.model` -- Light-ASD (Liao, Duan, Zhang, Li, Zhang, "A Light Weight Model for Active Speaker
  Detection", CVPR 2023), the authors' weights fine-tuned on TalkSet (`weight/finetuning_TalkSet.model` of
  https://github.com/Junhua-Liao/Light-ASD), copied unchanged; MIT licence, `LIGHT_ASD_LICENSE`. The network itself
  is `match_cuts/asd_model.py`.
