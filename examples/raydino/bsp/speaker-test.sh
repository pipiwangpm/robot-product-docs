# LOP 输出级 MUTE（关键：off 会全静音）
sudo amixer -c aic3104 cset name='Line Playback Volume'     9,9   
# 1,0代表card1 device0，播放音频
aplay -D hw:1,0 /tmp/mic_test.wav
