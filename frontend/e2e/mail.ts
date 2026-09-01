import { expect, type APIRequestContext } from "@playwright/test";

/** The composed stack's fake Resend, where every verification link lands. */
const MAIL_URL = process.env.ANCHOR_MAIL_URL ?? "http://localhost:8025";

/** The path of the latest verification link mailed to an address. */
export async function verificationPath(
  request: APIRequestContext,
  email: string,
): Promise<string> {
  const response = await request.get(`${MAIL_URL}/emails`);
  expect(response.ok()).toBe(true);
  const emails = (await response.json()) as { to: string[]; text: string }[];
  const message = emails.filter((candidate) => candidate.to.includes(email)).at(-1);
  expect(message, `no mail to ${email}`).toBeDefined();
  const match = /(\/verify\?token=[A-Za-z0-9_-]+)/.exec(message!.text);
  expect(match, message!.text).not.toBeNull();
  return match![1]!;
}
