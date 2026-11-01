/* Register the /app install worker only; this does not request notification permission or subscribe to push. */
(function () {
  if (!window.isSecureContext || !('serviceWorker' in navigator)) {
    window.tenantAppPwaReady = Promise.resolve(null);
    return;
  }
  window.tenantAppPwaReady = navigator.serviceWorker.register('/app/pwa-sw.js', {
    scope: '/app',
    updateViaCache: 'none'
  }).catch(error => {
    // The CRM remains usable if a browser or proxy blocks service worker registration.
    window.dispatchEvent(new CustomEvent('tenant-pwa-unavailable', {detail: {reason: 'registration-failed'}}));
    return null;
  });
})();
