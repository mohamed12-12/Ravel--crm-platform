import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

export class HandoffService {
  /**
   * Triggers a human handoff for a specific lead.
   * Kills AI automation for the user and logs the reason.
   */
  static async triggerHandoff(leadId: string, reason: string, priority: number = 1) {
    console.log(`[HANDOFF] Triggered for Lead ${leadId}. Reason: ${reason}`);

    // 1. Update Lead status to disable automation
    await prisma.lead.update({
      where: { id: leadId },
      data: {
        automationActive: false,
        handoffRequired: true,
        handoffReason: reason,
        status: 'BLOCKED' // Move to blocked status for review
      }
    });

    // 2. Add to Handoff Queue
    const queueEntry = await prisma.handoffQueue.create({
      data: {
        leadId,
        reason,
        priority
      }
    });

    // 3. TODO: Send real-time notification via WebSocket or SSE
    // For now, we simulate a webhook call to an external admin notifier
    this.notifyAdmin(leadId, reason);

    return queueEntry;
  }

  private static async notifyAdmin(leadId: string, reason: string) {
    // In a real system, this would push to a Redis queue or emit a socket event
    console.log(`[ALERT] Admin notified for Lead ${leadId}: ${reason}`);
  }
}
