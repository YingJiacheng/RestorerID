# RestorerID: Towards Tuning-Free Face Restoration with ID Preservation

### [Arxiv](https://arxiv.org/pdf/2411.14125)

## Install
```
# Create a conda environment and activate it
conda env create --file environments.yaml
conda activate RestorerID

# Install xformers
conda install xformers -c xformers/label/dev

# Install taming-transfomers
pip install -e git+https://github.com/CompVis/taming-transformers.git@master#egg=taming-transformers
pip install -e .
```

## Datasets
```
FFHQ
VGGFace2
Celeb-Ref
```


## Inference
```
# You can download the pretrained model from hugging-face and put it in ckpt path: ckpt/RestorerIDFull.ckpt
[RestorerID-huggingface](https://huggingface.co/YingJiacheng/RestorerID/)

# run
bash inference.sh
or
CUDA_VISIBLE_DEVICES=0 python scripts/Inference.py --LQpath TestSamples/1/lq1.png  --Refpath TestSamples/1/ref1.png  --Outputpath Results/1/
```

## Train 
```
# First train base model
# download sdv15 pretrained model (runwayml/v1-5-pruned.ckpt) from huggingface([RestorerID-huggingface](https://huggingface.co/YingJiacheng/RestorerID/)), put into the ckpt path as: ckpt/v1-5-pruned.ckpt
# prepare your datasets

CUDA_VISIBLE_DEVICES=0,1 python train_basemodel.py --train --base configs/v15/v15-BaseModel.yaml  --name v15_basemodel --scale_lr False

# Then train RestorerID
# rename your trained base model and put to ckpt path as: ckpt/basemodel.ckpt
# download ID model ip-adapter-faceid-plus_sd15.bin from [IPAdapter-huggingface](https://huggingface.co/h94/IP-Adapter-FaceID/tree/main)(or [RestorerID-huggingface](https://huggingface.co/YingJiacheng/RestorerID/)), put it into the ckpt path as: ckpt/ip-adapter-faceid-plus_sd15.bin
# prepare your datasets
# combine basemodel with ID model

python Combineckpt.py
CUDA_VISIBLE_DEVICES=0,1 python train_RestorerID.py --train --base configs/v15/v15-RestorerID.yaml  --name RestorerID --scale_lr False
```

## License
This project is released under the Apache 2.0 license.


## Acknowledgement
This work is mainly based on [StableSR](https://github.com/IceClear/StableSR), [IPAdapter](https://github.com/tencent-ailab/IP-Adapter), we thank the authors for the contribution.
