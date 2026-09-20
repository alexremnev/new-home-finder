// Support is an email address, not a workflow.
//
// This used to take a ticket in the chat: a prompt, the complaint, an optional
// email, a row in a table and a tab in the admin to read it. That is a
// helpdesk, and a helpdesk nobody staffs is worse than an address — somebody
// writes into it and waits for a reply no page is watching for. So /support now
// answers with the address and gets out of the way.
//
// 0030 drops the table too, so there is nothing half-removed to trip over.
export const SUPPORT_EMAIL = "support@londonhomefinder.co.uk";

export const SUPPORT_REPLY = [
  "Any questions at all — email us:",
  "",
  SUPPORT_EMAIL,
  "",
  "We answer within 24 hours.",
].join("\n");
