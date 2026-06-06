import axios from 'axios';

const api = axios.create({
  baseURL: 'http://localhost:3000/api',
  headers: {
    'Content-Type': 'application/json',
  },
});

export const CRM_V2 = {
  getHealth: () => api.get('/health'),
  
  // Module 2: Identity Resolution
  getDuplicates: () => api.get('/crm/duplicates'),
  resolveIdentity: (data: { masterId: string; aliasIds: string[] }) => 
    api.post('/crm/resolve-identity', data),
    
  // Module 3: Handoff Queue (To be implemented in middleware)
  getHandoffs: () => api.get('/handoffs'),
};

export default api;
