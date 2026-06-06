/**
 * Rahma Traveler CRM API Client
 */
const CRM_API = {
    version: 'v1',
    baseUrl: '/api/v1',

    async fetchStats() {
        const res = await fetch(`${this.baseUrl}/stats`);
        return await res.json();
    },

    async fetchCrmPreview(limit = 15) {
        const res = await fetch(`${this.baseUrl}/crm/preview?limit=${limit}`);
        return await res.json();
    },

    async interact(payload) {
        const res = await fetch(`${this.baseUrl}/agent/interact`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return await res.json();
    },

    async createBooking(payload) {
        const res = await fetch(`${this.baseUrl}/bookings/draft`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload)
        });
        return await res.json();
    },

    async reset() {
        const res = await fetch(`${this.baseUrl}/reset`, { method: 'POST' });
        return await res.json();
    }
};

/**
 * UI Utilities
 */
const UI = {
    formatDate(isoString) {
        if (!isoString) return 'N/A';
        try {
            const date = new Date(isoString);
            return date.toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
        } catch (e) {
            return isoString;
        }
    },

    showLoading(elementId) {
        const el = document.getElementById(elementId);
        if (el) el.classList.add('loading-shimmer');
    },

    hideLoading(elementId) {
        const el = document.getElementById(elementId);
        if (el) el.classList.remove('loading-shimmer');
    }
};

window.CRM_API = CRM_API;
window.UI = UI;
