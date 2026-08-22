if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/service-worker.js').catch(error => {
      console.warn('Service worker registration failed:', error);
    });
  });
}

let installPrompt;

function createInstallButton() {
  if (document.querySelector('.pwa-install')) return;

  const button = document.createElement('button');
  button.className = 'pwa-install';
  button.type = 'button';
  button.textContent = 'Install app';
  button.hidden = true;
  button.setAttribute('aria-label', 'Install SignalDesk');
  document.body.appendChild(button);

  button.addEventListener('click', async () => {
    if (installPrompt) {
      installPrompt.prompt();
      await installPrompt.userChoice;
      installPrompt = null;
      button.hidden = true;
      return;
    }

    const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
    if (isIos) {
      window.alert('To install SignalDesk, tap Share, then Add to Home Screen.');
    }
  });

  const isIos = /iphone|ipad|ipod/i.test(navigator.userAgent);
  const isStandalone = window.matchMedia('(display-mode: standalone)').matches || navigator.standalone;
  if (isIos && !isStandalone) button.hidden = false;
}

window.addEventListener('beforeinstallprompt', event => {
  event.preventDefault();
  installPrompt = event;
  createInstallButton();
  document.querySelector('.pwa-install').hidden = false;
});

window.addEventListener('appinstalled', () => {
  installPrompt = null;
  const button = document.querySelector('.pwa-install');
  if (button) button.remove();
});

window.addEventListener('DOMContentLoaded', createInstallButton);
