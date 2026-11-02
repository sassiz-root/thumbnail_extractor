wget https://github.com/xinntao/Real-ESRGAN/releases/download/v0.2.5.0/realesrgan-ncnn-vulkan-20220424-ubuntu.zip
unzip realesrgan-ncnn-vulkan-*.zip -d ~/realesrgan
chmod +x ~/realesrgan/realesrgan-ncnn-vulkan
sudo ln -s ~/realesrgan/realesrgan-ncnn-vulkan /usr/local/bin/
