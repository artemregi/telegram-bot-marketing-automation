/**
 * Bot Admin Panel — main.js
 * Global JS utilities for the admin panel.
 */

'use strict';

// ======================
// Confirm Delete Dialog
// ======================

/**
 * Show a confirmation dialog before deleting.
 * Use: onsubmit="return confirmDelete('Delete this item?')"
 */
function confirmDelete(message) {
  message = message || 'Вы уверены, что хотите удалить этот элемент? Действие необратимо.';
  return window.confirm(message);
}

// Attach confirm to all forms with data-confirm attribute
document.addEventListener('DOMContentLoaded', function () {
  document.querySelectorAll('form[data-confirm]').forEach(function (form) {
    form.addEventListener('submit', function (e) {
      const msg = form.getAttribute('data-confirm');
      if (!window.confirm(msg)) {
        e.preventDefault();
      }
    });
  });

  // Auto-dismiss alerts after 5 seconds
  document.querySelectorAll('.alert.alert-dismissible').forEach(function (alert) {
    setTimeout(function () {
      const bsAlert = bootstrap.Alert.getOrCreateInstance(alert);
      if (bsAlert) bsAlert.close();
    }, 5000);
  });

  // Highlight active nav link
  const currentPath = window.location.pathname;
  document.querySelectorAll('.navbar .nav-link').forEach(function (link) {
    const href = link.getAttribute('href');
    if (href && href !== '/' && currentPath.startsWith(href)) {
      link.classList.add('active');
    } else if (href === '/' && currentPath === '/') {
      link.classList.add('active');
    }
  });
});

// ======================
// File Preview on Upload
// ======================

/**
 * Show a thumbnail preview when a file is selected in an <input type="file">
 * Usage: add onchange="previewFile(this, 'previewElementId')" to the input
 */
function previewFile(input, previewId) {
  const preview = document.getElementById(previewId);
  if (!preview) return;

  if (input.files && input.files[0]) {
    const file = input.files[0];
    const ext = file.name.split('.').pop().toLowerCase();
    const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp', 'svg'];

    if (imageExts.includes(ext)) {
      const reader = new FileReader();
      reader.onload = function (e) {
        preview.src = e.target.result;
        preview.style.display = 'block';
      };
      reader.readAsDataURL(file);
    } else {
      preview.style.display = 'none';
    }
  }
}

// ======================
// Broadcast Page: Auto-Refresh
// ======================

/**
 * Auto-refresh broadcast status.
 * Called from broadcast.html template's inline script.
 * This file just exports utility functions; the interval is set in the template.
 */

// ======================
// Keyword Form: Toggle file path field visibility
// ======================

document.addEventListener('DOMContentLoaded', function () {
  const filePathInput = document.getElementById('file_path');
  const fileUploadInput = document.getElementById('fileUpload');

  if (fileUploadInput && filePathInput) {
    fileUploadInput.addEventListener('change', function () {
      const file = fileUploadInput.files[0];
      if (!file) return;

      // Show filename hint
      const ext = file.name.split('.').pop().toLowerCase();
      const imageExts = ['jpg', 'jpeg', 'png', 'gif', 'webp'];
      const previewDiv = document.getElementById('filePreview');
      const previewImg = document.getElementById('previewImg');

      if (previewDiv && previewImg && imageExts.includes(ext)) {
        const reader = new FileReader();
        reader.onload = function (e) {
          previewImg.src = e.target.result;
          previewImg.style.display = 'block';
          previewDiv.style.display = 'block';
        };
        reader.readAsDataURL(file);
      }
    });
  }
});

// ======================
// Toast Notification Utility
// ======================

/**
 * Show a temporary toast notification (optional).
 */
function showToast(message, type) {
  type = type || 'info';
  const colors = {
    success: '#3fb950',
    danger:  '#f85149',
    warning: '#d29922',
    info:    '#58a6ff',
  };
  const color = colors[type] || colors.info;

  const toast = document.createElement('div');
  toast.style.cssText = `
    position: fixed;
    bottom: 1.5rem;
    right: 1.5rem;
    background: #161b22;
    border: 1px solid ${color};
    color: #e6edf3;
    padding: 0.75rem 1.25rem;
    border-radius: 8px;
    font-size: 0.9rem;
    z-index: 9999;
    box-shadow: 0 4px 12px rgba(0,0,0,0.4);
    transition: opacity 0.3s;
    max-width: 320px;
  `;
  toast.textContent = message;
  document.body.appendChild(toast);

  setTimeout(function () {
    toast.style.opacity = '0';
    setTimeout(function () {
      if (toast.parentNode) toast.parentNode.removeChild(toast);
    }, 300);
  }, 3500);
}
