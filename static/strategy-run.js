document.querySelectorAll('.strategy-page-run').forEach(button => {
  const poll = () => fetch(button.dataset.statusUrl)
    .then(response => response.json())
    .then(status => {
      if (!status.refreshing) {
        location.reload();
        return;
      }
      setTimeout(poll, 1500);
    })
    .catch(() => setTimeout(poll, 2500));

  button.addEventListener('click', async () => {
    button.disabled = true;
    button.textContent = 'Starting…';
    try {
      const response = await fetch(button.dataset.runUrl, {method: 'POST'});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Could not start strategy');
      button.textContent = 'Running…';
      setTimeout(poll, 800);
    } catch (error) {
      button.disabled = false;
      button.textContent = 'Try again';
      button.title = error.message;
    }
  });
});
