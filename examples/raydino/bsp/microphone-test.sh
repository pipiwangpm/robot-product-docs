# 设置音量
sudo amixer -c aic3104 cset name='PGA Capture Volume' 60,60   # 0..119，约 +30 dB
# -d 录音时间（秒）
arecord -D hw:1,0 -f cd -d 10 -vvv /tmp/mic_test.wav
