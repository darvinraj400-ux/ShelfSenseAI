#!/bin/bash
set -e
pip install -r requirements.txt
mkdir -p /tmp/ml
curl -L -o /tmp/ml/pricing_model.pkl "https://huggingface.co/theonlyoddzone/shelfsense-pricing-model/resolve/main/pricing_model.pkl"
echo "Build complete. Model downloaded to /tmp/ml/pricing_model.pkl"
