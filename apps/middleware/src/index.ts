import express, { Request, Response } from 'express';
import dotenv from 'dotenv';
import cors from 'cors';
import { IdentityController } from './controllers/identity.controller';

dotenv.config();

const app = express();
const port = process.env.PORT || 3000;

app.use(cors());
app.use(express.json());

// TODO(production): add auth, request rate limiting, request IDs, and structured audit logs
// before exposing this middleware beyond local/admin networks.

// API v2 Routes
app.post('/api/crm/resolve-identity', IdentityController.resolveIdentity);
app.get('/api/crm/duplicates', IdentityController.getDuplicates);

// Basic Health Check
app.get('/api/health', (req: Request, res: Response) => {
  res.json({
    status: 'ok',
    version: '2.0.0-middleware',
    timestamp: new Date().toISOString()
  });
});

app.listen(port, () => {
  console.log(`[V2 Middleware] Server is running at http://0.0.0.0:${port}`);
});
