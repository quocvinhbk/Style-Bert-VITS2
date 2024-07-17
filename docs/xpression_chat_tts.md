### Add japanese model:

#### 1. Clone model data to local:

```bash
brew install git-lfs
git lfs install
git clone https://huggingface.co/spaces/MakiAi/Style-Bert-VITS2-JVNV
```

#### 2. Save pytorch_model

```bash
cp -R Style-Bert-VITS2-JVNV/bert/deberta-v2-large-japanese-char-wwm xpression_chat_tts/bert/deberta-v2-large-japanese-char-wwm
```

#### 3. Save model_assets

```bash
cp -R Style-Bert-VITS2-JVNV/model_assets xpression_chat_tts/model_assets
```