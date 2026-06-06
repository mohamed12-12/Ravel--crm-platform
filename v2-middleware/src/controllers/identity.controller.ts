import { Request, Response } from 'express';
import { z } from 'zod';

// Strict Zod validation for Identity Resolution matching Flask core
const ResolveIdentitySchema = z.object({
  masterId: z.string(),
  aliasIds: z.array(z.string()).max(50, "Cannot link more than 50 travelers at once to prevent DB locks")
});

const FLASK_API_URL = process.env.FLASK_API_URL || 'http://localhost:5000/api/crm';

export class IdentityController {
  /**
   * Fetches potential duplicates directly from Flask V1 Core SQLite database.
   */
  static async getDuplicates(req: Request, res: Response) {
    try {
      const response = await fetch(`${FLASK_API_URL}/duplicates`);
      if (!response.ok) {
        throw new Error(`Flask backend returned status ${response.status}`);
      }
      const data = await response.json();
      return res.json(data);
    } catch (error: any) {
      console.error('[V2 Middleware] Get Duplicates Error:', error.message);
      return res.status(500).json({ error: 'Failed to fetch duplicates from V1 Core' });
    }
  }

  /**
   * Bridges identity resolution requests to Flask V1 Core SQLite.
   * This executes the SQLAlchemy transactional merge and mirrors the update to Excel/Google Sheets.
   */
  static async resolveIdentity(req: Request, res: Response) {
    try {
      const { masterId, aliasIds } = ResolveIdentitySchema.parse(req.body);

      const response = await fetch(`${FLASK_API_URL}/resolve-identity`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json'
        },
        body: JSON.stringify({
          master_id: masterId,
          alias_ids: aliasIds
        })
      });

      const data = await response.json();

      if (!response.ok) {
        return res.status(response.status).json(data);
      }

      return res.json({
        success: true,
        message: data.message || `Successfully merged aliases into traveler ${masterId}`,
        details: data.details
      });

    } catch (error) {
      if (error instanceof z.ZodError) {
        return res.status(400).json({ error: 'Validation Failed', details: error.errors });
      }
      console.error('[V2 Middleware] Identity Resolution Error:', error);
      return res.status(500).json({ error: 'Internal Server Error' });
    }
  }
}
