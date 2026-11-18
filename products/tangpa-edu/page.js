const dialog = document.querySelector('#video-dialog');
const player = dialog.querySelector('video');
const title = dialog.querySelector('#video-title');

document.querySelectorAll('[data-video]').forEach((button) => {
  button.addEventListener('click', () => {
    title.textContent = button.dataset.title;
    player.src = button.dataset.video;
    dialog.showModal();
    player.play().catch(() => {
      // Native controls remain available if automatic playback is blocked.
    });
  });
});

dialog.addEventListener('close', () => {
  player.pause();
  player.removeAttribute('src');
  player.load();
});

