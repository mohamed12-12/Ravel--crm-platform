// app/static/js/main.js
document.addEventListener('DOMContentLoaded', () => {
    console.log('Rahma CRM Loaded');
    
    // Auto-dismiss flashes
    const alerts = document.querySelectorAll('.alert');
    alerts.forEach(alert => {
        setTimeout(() => {
            alert.style.opacity = '0';
            setTimeout(() => alert.remove(), 500);
        }, 5000);
    });
});
