import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

export class CopyService {
  /**
   * Fetches an approved template and interpolates it with dynamic data.
   * This ensures the AI never generates its own text.
   */
  static async getApprovedResponse(
    templateId: string,
    lang: 'ar' | 'en',
    variables: Record<string, string | number>
  ): Promise<string> {
    const template = await prisma.copyLibrary.findUnique({
      where: { id: templateId }
    });

    if (!template) {
      throw new Error(`CRITICAL: Template ID ${templateId} not found in CopyLibrary.`);
    }

    let text = lang === 'ar' ? template.ar_text : template.en_text;

    // Interpolation Logic
    for (const [key, value] of Object.entries(variables)) {
      const placeholder = `{${key}}`;
      text = text.replace(new RegExp(placeholder, 'g'), String(value));
    }

    // Check if any placeholders remain (indicating missing data)
    if (text.includes('{') && text.includes('}')) {
      console.warn(`WARNING: Missing interpolation values for template ${templateId}: ${text}`);
    }

    return text;
  }
}
