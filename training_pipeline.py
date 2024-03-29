# -*- coding: utf-8 -*-
"""Training pipeline.ipynb

Automatically generated by Colaboratory.

Original file is located at
    https://colab.research.google.com/drive/1tHYCLatdvrn_4o26HwECEIYwHpZp6U8K
"""

gpu_info = !nvidia-smi
gpu_info = '\n'.join(gpu_info)
if gpu_info.find('failed') >= 0:
  print('Not connected to a GPU')
else:
  print(gpu_info)

!add-apt-repository -y ppa:jonathonf/ffmpeg-4
!apt update
!apt install -y ffmpeg

from google.colab import drive
drive.mount('/content/drive')

!pip install datasets>=2.6.1
!pip install git+https://github.com/huggingface/transformers
!pip install librosa
!pip install evaluate>=0.30
!pip install jiwer
!pip install gradio
!pip install hopsworks
!pip install modal

import datasets
import hopsworks
from transformers import WhisperTokenizer
from huggingface_hub import login, notebook_login

login(token="hf_*")
notebook_login()

# You have to set the environment variable 'HOPSWORKS_API_KEY' for login to succeed
project = hopsworks.login(api_key_value="*")
# Get data from hopswork
#dataset_api = project.get_dataset_api()
#path = dataset_api.download(overwrite=True, path="tilos/cantonese_processed")  #download to local. Return a path

# Get data from huggingface
from datasets import load_dataset, DatasetDict
common_voice = DatasetDict()
common_voice = load_dataset("tilos/cantonese_processed_guangzhou") #dataset

cantonese_voice = common_voice['train'].train_test_split(test_size=0.2, shuffle=True) #[?] train and test

print(cantonese_voice)

from transformers import WhisperProcessor
processor = WhisperProcessor.from_pretrained("openai/whisper-small", language="zh-HK", task="transcribe")

import torch
from dataclasses import dataclass
from typing import Any, Dict, List, Union

#Define a Data collator
@dataclass
class DataCollatorSpeechSeq2SeqWithPadding:
    processor: Any
    def __call__(self, features: List[Dict[str, Union[List[int], torch.Tensor]]]) -> Dict[str, torch.Tensor]:
        # split inputs and labels since they have to be of different lengths and need different padding methods
        # first treat the audio inputs by simply returning torch tensors

        #print(features) # features is a list "nriat"?
        input_features = [ {"input_features": feature["input_features"]} for feature in features]
        #input_features = [ {"input_features": feature["input_features"]} for feature in features] #string indices must be integers/ original code

        batch = self.processor.feature_extractor.pad(input_features, return_tensors="pt")

        # get the tokenized label sequences
        label_features = [{"input_ids": feature["labels"]} for feature in features]
        # pad the labels to max length
        labels_batch = self.processor.tokenizer.pad(label_features, return_tensors="pt")

        # replace padding with -100 to ignore loss correctly
        labels = labels_batch["input_ids"].masked_fill(labels_batch.attention_mask.ne(1), -100)

        # if bos token is appended in previous tokenization step,
        # cut bos token here as it's append later anyways
        if (labels[:, 0] == self.processor.tokenizer.bos_token_id).all().cpu().item():
            labels = labels[:, 1:]

        batch["labels"] = labels

        return batch
data_collator = DataCollatorSpeechSeq2SeqWithPadding(processor=processor)

#Elvaluation matrics
import evaluate
tokenizer = WhisperTokenizer.from_pretrained("openai/whisper-small", language="zh-HK", task="transcribe")
metric = evaluate.load("wer")
def compute_metrics(pred):
    pred_ids = pred.predictions
    label_ids = pred.label_ids

    # replace -100 with the pad_token_id
    label_ids[label_ids == -100] = tokenizer.pad_token_id

    # we do not want to group tokens when computing the metrics
    pred_str = tokenizer.batch_decode(pred_ids, skip_special_tokens=True)
    label_str = tokenizer.batch_decode(label_ids, skip_special_tokens=True)

    wer = 100 * metric.compute(predictions=pred_str, references=label_str)

    return {"wer": wer}

#Load a pre-trained checkpoint
from transformers import WhisperForConditionalGeneration
try:
  model = WhisperForConditionalGeneration.from_pretrained("tilos/whisper-small-zh-HK")
except:
  model = WhisperForConditionalGeneration.from_pretrained("openai/whisper-small")
model.config.forced_decoder_ids = None
model.config.suppress_tokens = []

#Define the training configuration
from transformers import Seq2SeqTrainingArguments
training_args = Seq2SeqTrainingArguments(
    output_dir="./whisper-small-zh-HK",  # change to a repo name of your choice
    per_device_train_batch_size=16,
    gradient_accumulation_steps=1,  # increase by 2x for every 2x decrease in batch size
    learning_rate=1e-5,
    warmup_steps=500,
    max_steps=4000,
    gradient_checkpointing=True,
    fp16=True,
    evaluation_strategy="steps",
    per_device_eval_batch_size=8,
    predict_with_generate=True,
    generation_max_length=225,
    save_steps=1000,
    eval_steps=1000,
    logging_steps=25,
    report_to=["tensorboard"],
    load_best_model_at_end=True,
    metric_for_best_model="wer",
    greater_is_better=False,
    push_to_hub=True,
)
#Forward the training arguments to huggingface
from transformers import Seq2SeqTrainer
trainer = Seq2SeqTrainer(
    args=training_args,
    model=model,
    train_dataset=cantonese_voice["train"], #["train"],
    eval_dataset=cantonese_voice["test"],   #["test"],
    data_collator=data_collator,
    compute_metrics=compute_metrics,
    tokenizer=processor.feature_extractor,
    )
processor.save_pretrained(training_args.output_dir)

#Training start

print(cantonese_voice["train"][0])
print(cantonese_voice["train"])
print(cantonese_voice)
trainer.train()


kwargs = {
    "dataset_tags": "mozilla-foundation/common_voice_11_0",
    "dataset": "Common Voice 11.0",  # a 'pretty' name for the training dataset
    "dataset_args": "config: zh, split: test",
    "language": "zh",
    "model_name": "Whisper Small zh-HK - Ziyou Li",  # a 'pretty' name for our model
    "finetuned_from": "openai/whisper-small",
    "tasks": "automatic-speech-recognition",
    "tags": "hf-asr-leaderboard",
    }
trainer.push_to_hub(**kwargs)

from transformers import pipeline
import gradio as gr

pipe = pipeline(model="tilos/whisper-small-zh-HK")  # change to "your-username/the-name-you-picked"

def transcribe(audio):
    text = pipe(audio)["text"]
    return text

iface = gr.Interface(
    fn=transcribe, 
    inputs=gr.Audio(source="microphone", type="filepath"), 
    outputs="text",
    title="Whisper Small Cantonese",
    description="Realtime demo for Cantonese speech recognition using a fine-tuned Whisper small model.",
)

iface.launch()
