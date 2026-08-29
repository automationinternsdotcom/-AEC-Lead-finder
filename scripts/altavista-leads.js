// AltaVista lead -> Pipedrive deal automation.
// Reads the currently synced Pipedrive mailbox, identifies forwarded AltaVista
// lead messages, and creates Organization -> Person -> Deal records.

const crypto = require('crypto');
const fs = require('fs');
const path = require('path');

const PIPEDRIVE_DOMAIN_DEFAULT = 'aether';
const ALTAVISTA_SUBJECT_MATCH_DEFAULT = 'AltaVista Client Opportunity';
const ALTAVISTA_BATCH_SIZE_DEFAULT = 25;
const ALTAVISTA_MAX_AGE_HOURS_DEFAULT = 168;
const ALTAVISTA_MARKERS = ['altavista', 'alta vista', 'altavistasp.com'];
const ALTAVISTA_SOURCE_EMAILS = ['mward@altavistasp.com'];

const FIELD_LABELS = [
  { key: 'name', label: 'Name' },
  { key: 'company', label: 'Company' },
  { key: 'title', label: 'Title' },
  { key: 'phone', label: 'Phone Number' },
  { key: 'email', label: 'Email' },
  { key: 'address', label: 'Property Address' },
  { key: 'background', label: 'Company Background' },
  { key: 'inquiry', label: 'Inquiry' },
];

function loadDotEnv(filePath = path.join(process.cwd(), '.env')) {
  if (!fs.existsSync(filePath)) return;

  const lines = fs.readFileSync(filePath, 'utf8').split(/\r?\n/);
  for (const line of lines) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const match = trimmed.match(/^([A-Za-z_][A-Za-z0-9_]*)=(.*)$/);
    if (!match || process.env[match[1]] !== undefined) continue;
    process.env[match[1]] = stripEnvQuotes(match[2].trim());
  }
}

function stripEnvQuotes(value) {
  if ((value.startsWith('"') && value.endsWith('"')) || (value.startsWith("'") && value.endsWith("'"))) {
    return value.slice(1, -1);
  }
  return value;
}

function requireEnv(env, key, missing) {
  if (!env[key]) missing.push(key);
  return env[key] || '';
}

function parsePositiveInt(value, fallback) {
  const parsed = parseInt(value || '', 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function loadConfig() {
  loadDotEnv();

  const env = process.env;
  const missing = [];
  const pipedriveToken = requireEnv(env, 'PIPEDRIVE_API_TOKEN', missing);
  const pipelineId = requireEnv(env, 'PIPEDRIVE_PIPELINE_ID', missing);
  const intakeKeyField = requireEnv(env, 'ALTAVISTA_INTAKE_KEY_FIELD', missing);

  if (missing.length) {
    throw new Error('Missing required configuration: ' + missing.join(', '));
  }

  return {
    pipedriveDomain: normalizePipedriveDomain(env.PIPEDRIVE_DOMAIN || PIPEDRIVE_DOMAIN_DEFAULT),
    pipedriveToken,
    pipelineId: Number(pipelineId),
    stageId: env.PIPEDRIVE_STAGE_ID ? Number(env.PIPEDRIVE_STAGE_ID) : null,
    intakeKeyField,
    subjectMatch: env.ALTAVISTA_SUBJECT_MATCH || ALTAVISTA_SUBJECT_MATCH_DEFAULT,
    maxAgeHours: parsePositiveInt(env.ALTAVISTA_MAX_AGE_HOURS, ALTAVISTA_MAX_AGE_HOURS_DEFAULT),
    batchSize: parsePositiveInt(env.ALTAVISTA_BATCH_SIZE, ALTAVISTA_BATCH_SIZE_DEFAULT),
  };
}

function normalizePipedriveDomain(value) {
  return String(value || '')
    .trim()
    .replace(/^https?:\/\//i, '')
    .replace(/\.pipedrive\.com.*$/i, '')
    .replace(/\/.*$/, '');
}

function pipeUrl(cfg, pathPart) {
  return 'https://' + cfg.pipedriveDomain + '.pipedrive.com' + pathPart;
}

async function callPipedriveJson(method, apiPath, payload, cfg) {
  const response = await fetch(pipeUrl(cfg, apiPath), {
    method,
    headers: {
      'x-api-token': cfg.pipedriveToken,
      Accept: 'application/json',
      'Content-Type': 'application/json',
    },
    body: payload ? JSON.stringify(payload) : undefined,
  });
  return parsePipedriveResponse(response, method, apiPath);
}

async function callPipedriveForm(method, apiPath, payload, cfg) {
  const body = new URLSearchParams();
  for (const [key, value] of Object.entries(payload || {})) {
    if (value !== undefined && value !== null) body.set(key, String(value));
  }

  const response = await fetch(pipeUrl(cfg, apiPath), {
    method,
    headers: {
      'x-api-token': cfg.pipedriveToken,
      Accept: 'application/json',
      'Content-Type': 'application/x-www-form-urlencoded',
    },
    body,
  });
  return parsePipedriveResponse(response, method, apiPath);
}

async function parsePipedriveResponse(response, method, apiPath) {
  const text = await response.text();
  let body = {};
  try {
    body = text ? JSON.parse(text) : {};
  } catch (err) {
    body = { raw: text };
  }

  if (response.ok && body.success !== false) return body;
  if (response.status === 429) throw new Error('429: Pipedrive API budget exhausted; will retry next run');

  const message = body.error || body.error_info || body.statusText || body.raw || 'HTTP ' + response.status;
  throw new Error(method + ' ' + apiPath + ' failed [' + response.status + ']: ' + message);
}

function query(params) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== '') search.set(key, String(value));
  }
  return search.toString();
}

async function listInboxThreads(cfg) {
  const result = await callPipedriveJson(
    'GET',
    '/api/v1/mailbox/mailThreads?' + query({ folder: 'inbox', start: 0, limit: cfg.batchSize }),
    null,
    cfg
  );
  return result.data || [];
}

async function listThreadMessages(threadId, cfg) {
  const result = await callPipedriveJson('GET', '/api/v1/mailbox/mailThreads/' + threadId + '/mailMessages', null, cfg);
  return result.data || [];
}

async function fetchMailMessage(messageId, cfg) {
  const result = await callPipedriveJson(
    'GET',
    '/api/v1/mailbox/mailMessages/' + messageId + '?' + query({ include_body: 1 }),
    null,
    cfg
  );
  return result.data || {};
}

async function updateMailThread(threadId, dealId, cfg) {
  await callPipedriveForm(
    'PUT',
    '/api/v1/mailbox/mailThreads/' + threadId,
    { deal_id: dealId, shared_flag: 1, read_flag: 1, archived_flag: 1 },
    cfg
  );
  console.log('Linked mailbox thread %s to deal %s, shared it, marked it read, and archived it', threadId, dealId);
}

function normalizeSubject(subject) {
  let value = String(subject || '').toLowerCase();
  value = value.replace(/\[[^\]]*\]/g, ' ');
  value = value.replace(/^(\s*(re|fw|fwd)\s*:\s*)+/i, '');
  return normalizeText(value);
}

function subjectMatches(subject, expected) {
  const actual = normalizeSubject(subject);
  const target = normalizeSubject(expected);
  return Boolean(actual && target && (actual === target || actual.includes(target)));
}

function normalizeText(value) {
  return String(value || '').replace(/\s+/g, ' ').trim().toLowerCase();
}

function htmlToText(value) {
  return decodeHtmlEntities(
    String(value || '')
      .replace(/<\s*br\s*\/?>/gi, '\n')
      .replace(/<\/(p|div|li|tr|h[1-6])>/gi, '\n')
      .replace(/<style[\s\S]*?<\/style>/gi, '')
      .replace(/<script[\s\S]*?<\/script>/gi, '')
      .replace(/<[^>]+>/g, ' ')
  )
    .replace(/\u00a0/g, ' ')
    .replace(/[ \t]+\n/g, '\n')
    .replace(/\n[ \t]+/g, '\n')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

function decodeHtmlEntities(value) {
  return String(value || '')
    .replace(/&nbsp;/gi, ' ')
    .replace(/&amp;/gi, '&')
    .replace(/&lt;/gi, '<')
    .replace(/&gt;/gi, '>')
    .replace(/&quot;/gi, '"')
    .replace(/&#39;/gi, "'")
    .replace(/&#(\d+);/g, (_, code) => String.fromCharCode(Number(code)));
}

function parseAvFields(body) {
  const text = htmlToText(body).replace(/\r\n/g, '\n');
  const positions = [];

  for (const field of FIELD_LABELS) {
    const re = new RegExp('^[ \\t]*' + escapeRegExp(field.label) + ':[ \\t]*', 'im');
    const match = re.exec(text);
    if (match) positions.push({ key: field.key, start: match.index, valueStart: match.index + match[0].length });
  }

  positions.sort((a, b) => a.start - b.start);

  const result = {};
  for (let i = 0; i < positions.length; i++) {
    const end = i + 1 < positions.length ? positions[i + 1].start : text.length;
    result[positions[i].key] = text.slice(positions[i].valueStart, end).trim();
  }

  if (result.email) result.email = normalizeEmail(result.email);
  return result;
}

function escapeRegExp(value) {
  return String(value).replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function hasAltaVistaBodyMarker(body) {
  const normalized = normalizeText(htmlToText(body));
  return ALTAVISTA_MARKERS.some((marker) => normalized.includes(marker));
}

function extractEmails(value) {
  const matches = String(value || '').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/gi);
  return matches ? matches.map((email) => normalizeEmail(email)).filter(Boolean) : [];
}

function partyEmails(parties) {
  const list = Array.isArray(parties) ? parties : [parties];
  const emails = [];

  for (const party of list) {
    if (!party) continue;
    if (typeof party === 'object') {
      emails.push(...extractEmails(party.email_address || party.email || ''));
      emails.push(...extractEmails(party.name || ''));
    } else {
      emails.push(...extractEmails(party));
    }
  }

  return emails;
}

function hasAltaVistaSource(message) {
  const emails = [
    ...partyEmails(message.from),
    ...partyEmails(message.sender),
    ...partyEmails(message.reply_to || message.replyTo),
    ...extractEmails(message.body || message.snippet || ''),
  ];

  return emails.some((email) => ALTAVISTA_SOURCE_EMAILS.includes(email));
}

function shouldInspectMessageSummary(summary, thread, cfg) {
  const subject = summary.subject || thread.subject || '';
  return (
    subjectMatches(subject, cfg.subjectMatch) ||
    hasAltaVistaSource(summary) ||
    hasAltaVistaBodyMarker(summary.snippet || '')
  );
}

function isAltaVistaMessage(message, fields, cfg) {
  const hasExpectedFields = Boolean(fields.name && fields.email && fields.inquiry);
  const hasExpectedSource = hasAltaVistaSource(message);

  return (
    hasExpectedFields &&
    (
      hasExpectedSource ||
      (
        subjectMatches(message.subject, cfg.subjectMatch) &&
        hasAltaVistaBodyMarker(message.body || message.snippet || '')
      )
    )
  );
}

function parsePartyNameAndEmail(parties) {
  const first = Array.isArray(parties) ? parties[0] : parties;
  if (first && typeof first === 'object') {
    return {
      name: String(first.name || first.email_address || '').trim(),
      email: normalizeEmail(first.email_address || first.email || ''),
    };
  }

  const raw = String(first || '');
  const match = raw.match(/^([^<]*?)\s*<([^>]+)>/);
  if (match) return { name: match[1].trim() || match[2], email: normalizeEmail(match[2]) };
  return { name: raw.trim(), email: normalizeEmail(raw) };
}

function normalizeEmail(email) {
  const match = String(email || '').match(/[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}/i);
  return match ? match[0].trim().replace(/^mailto:/i, '').toLowerCase() : '';
}

function organizationNameFor(fields, email) {
  if (fields.company) return cleanSingleLine(fields.company).slice(0, 255);
  const domain = email.split('@')[1] || '';
  if (domain) return domain.replace(/^www\./i, '').split('.')[0].replace(/[-_]+/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
  return cleanSingleLine(fields.name || 'AltaVista Lead Organization').slice(0, 255);
}

function cleanSingleLine(value) {
  return String(value || '').replace(/\s+/g, ' ').trim();
}

function buildIntakeKey(fields, message, cfg) {
  const stableParts = [
    normalizeSubject(cfg.subjectMatch),
    normalizeEmail(fields.email),
    normalizeText(fields.name),
    normalizeText(fields.company),
    normalizeText(fields.address),
    normalizeText(fields.inquiry),
  ];
  const digest = crypto.createHash('sha256').update(stableParts.join('|')).digest('hex').slice(0, 40);
  return 'altavista:' + digest;
}

function escapeHtml(value) {
  return String(value || '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

async function findOrCreateOrganization(name, cfg) {
  const search = await callPipedriveJson(
    'GET',
    '/api/v2/organizations/search?' + query({ term: name, fields: 'name', exact_match: true, limit: 1 }),
    null,
    cfg
  );
  const match = firstSearchItem(search);
  if (match && match.id) return Number(match.id);

  const org = (await callPipedriveJson('POST', '/api/v2/organizations', { name }, cfg)).data;
  console.log('Created organization %s for "%s"', org.id, name);
  return Number(org.id);
}

async function findOrCreatePerson(fields, orgId, cfg) {
  const name = cleanSingleLine(fields.name || fields.email);
  const email = normalizeEmail(fields.email);
  const search = await callPipedriveJson(
    'GET',
    '/api/v2/persons/search?' + query({ term: email, fields: 'email', exact_match: true, limit: 1 }),
    null,
    cfg
  );
  const match = firstSearchItem(search);

  if (match && match.id) {
    const existingOrgId = Number(match.org_id || match.organization_id || (match.organization && match.organization.id) || 0);
    if (orgId && existingOrgId !== orgId) {
      await callPipedriveJson('PATCH', '/api/v2/persons/' + match.id, { org_id: orgId }, cfg);
      console.log('Linked existing person %s to organization %s', match.id, orgId);
    }
    return Number(match.id);
  }

  const payload = {
    name,
    org_id: orgId,
    emails: [{ value: email, primary: true, label: 'work' }],
  };
  if (fields.phone) payload.phones = [{ value: cleanSingleLine(fields.phone), primary: true, label: 'work' }];

  const person = (await callPipedriveJson('POST', '/api/v2/persons', payload, cfg)).data;
  console.log('Created person %s for %s <%s>', person.id, name, email);
  return Number(person.id);
}

function firstSearchItem(searchResponse) {
  const items = searchResponse && searchResponse.data && Array.isArray(searchResponse.data.items) ? searchResponse.data.items : [];
  if (!items.length) return null;
  return items[0].item || items[0];
}

async function findExistingDealByIntakeKey(intakeKey, cfg) {
  const search = await callPipedriveJson(
    'GET',
    '/api/v2/deals/search?' + query({ term: intakeKey, fields: 'custom_fields', exact_match: true, limit: 10 }),
    null,
    cfg
  );
  const items = search.data && Array.isArray(search.data.items) ? search.data.items : [];

  for (const entry of items) {
    const item = entry.item || entry;
    const fields = item.custom_fields || item;
    if (fields && fields[cfg.intakeKeyField] === intakeKey) return item;
  }

  return null;
}

async function createDeal(fields, orgId, personId, intakeKey, cfg) {
  const title = cleanSingleLine('AltaVista Lead: ' + (fields.company || fields.name || fields.email)).slice(0, 255);
  const payload = {
    title,
    person_id: personId,
    org_id: orgId,
    pipeline_id: cfg.pipelineId,
    custom_fields: { [cfg.intakeKeyField]: intakeKey },
  };
  if (cfg.stageId) payload.stage_id = cfg.stageId;

  const deal = (await callPipedriveJson('POST', '/api/v2/deals', payload, cfg)).data;
  console.log('Created deal %s with AltaVista intake key %s', deal.id, intakeKey);
  await addDealNote(deal.id, fields, intakeKey, cfg);
  return Number(deal.id);
}

async function addDealNote(dealId, fields, intakeKey, cfg) {
  const lines = [
    '<b>AltaVista lead intake (automated)</b>',
    '<b>AltaVista Intake Key:</b> ' + escapeHtml(intakeKey),
    '<b>Name:</b> ' + escapeHtml(fields.name),
    '<b>Company:</b> ' + escapeHtml(fields.company || 'Not found'),
    '<b>Title:</b> ' + escapeHtml(fields.title || 'Not found'),
    '<b>Phone:</b> ' + escapeHtml(fields.phone || 'Not found'),
    '<b>Email:</b> ' + escapeHtml(fields.email),
    '<b>Property address:</b> ' + escapeHtml(fields.address || 'Not found'),
    '<b>Company background:</b> ' + escapeHtml(fields.background || 'Not found'),
    '<b>Inquiry:</b> ' + escapeHtml(fields.inquiry),
  ];

  const note = (await callPipedriveJson(
    'POST',
    '/api/v1/notes',
    { content: lines.join('<br>'), deal_id: dealId, pinned_to_deal_flag: 1 },
    cfg
  )).data;
  console.log('Added note %s to deal %s', note.id, dealId);
}

async function processAltaVistaMessage(message, cfg) {
  const fields = parseAvFields(message.body || '');
  if (!isAltaVistaMessage(message, fields, cfg)) return null;

  const receivedAt = Date.parse(message.message_time || message.add_time || message.update_time || '');
  if (Number.isFinite(receivedAt) && Date.now() - receivedAt > cfg.maxAgeHours * 60 * 60 * 1000) {
    console.log('Skipped message %s: older than %d hours', message.id, cfg.maxAgeHours);
    return null;
  }

  const from = parsePartyNameAndEmail(message.from);
  fields.email = normalizeEmail(fields.email || from.email);
  if (!fields.email) {
    console.log('Skipped message %s: AltaVista fields did not include a valid email', message.id);
    return null;
  }

  const intakeKey = buildIntakeKey(fields, message, cfg);
  const existingDeal = await findExistingDealByIntakeKey(intakeKey, cfg);
  if (existingDeal && existingDeal.id) {
    console.log('Deal %s already exists for AltaVista intake key %s', existingDeal.id, intakeKey);
    return Number(existingDeal.id);
  }

  const orgId = await findOrCreateOrganization(organizationNameFor(fields, fields.email), cfg);
  const personId = await findOrCreatePerson(fields, orgId, cfg);
  return createDeal(fields, orgId, personId, intakeKey, cfg);
}

async function processThread(thread, cfg) {
  const summaries = await listThreadMessages(thread.id, cfg);
  let matchedCount = 0;
  let successCount = 0;
  let lastDealId = null;

  for (const summary of summaries) {
    const subject = summary.subject || thread.subject || '';
    if (!shouldInspectMessageSummary(summary, thread, cfg)) continue;

    const message = await fetchMailMessage(summary.id, cfg);
    message.subject = message.subject || subject;

    const fields = parseAvFields(message.body || '');
    if (!isAltaVistaMessage(message, fields, cfg)) continue;

    matchedCount++;
    const dealId = await processAltaVistaMessage(message, cfg);
    if (dealId) {
      successCount++;
      lastDealId = dealId;
    }
  }

  if (matchedCount > 0 && successCount === matchedCount && lastDealId) {
    await updateMailThread(thread.id, lastDealId, cfg);
  } else if (matchedCount > successCount) {
    console.log('Left mailbox thread %s in inbox because %d of %d AltaVista message(s) failed', thread.id, matchedCount - successCount, matchedCount);
  }

  return { matchedCount, successCount };
}

async function main() {
  const cfg = loadConfig();
  const threads = await listInboxThreads(cfg);
  console.log('Found %d inbox thread(s) to inspect', threads.length);

  let matched = 0;
  let processed = 0;
  for (const thread of threads) {
    try {
      const result = await processThread(thread, cfg);
      matched += result.matchedCount;
      processed += result.successCount;
    } catch (err) {
      console.log('Warn: failed to process mailbox thread %s (%s)', thread.id, err.message);
    }
  }

  console.log('Run finished: %d AltaVista message(s) matched, %d processed', matched, processed);
}

if (require.main === module) {
  main().catch((err) => {
    console.error('Fatal error:', err);
    process.exitCode = 1;
  });
}

module.exports = {
  loadConfig,
  normalizeSubject,
  subjectMatches,
  htmlToText,
  parseAvFields,
  normalizeEmail,
  buildIntakeKey,
  isAltaVistaMessage,
  hasAltaVistaSource,
  shouldInspectMessageSummary,
  organizationNameFor,
  escapeHtml,
};
