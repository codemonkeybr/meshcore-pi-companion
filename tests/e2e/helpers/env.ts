/**
 * Centralized E2E environment configuration.
 *
 * All environment-dependent values live here with sensible defaults that
 * match the maintainer's test rig. Contributors can override any of these
 * via environment variables to match their own hardware setup.
 *
 * See CONTRIBUTING.md § "E2E Testing" for what each variable means and
 * how to set up a test environment from scratch.
 */

/**
 * Channel used to trigger echo-bot traffic generation.
 *
 * The echo bot (running on a second "partner" radio) should monitor this
 * channel and reply to any message, generating incoming RF traffic that
 * mesh-traffic tests can observe. The channel is created automatically if
 * it doesn't exist in the test database.
 */
export const E2E_ECHO_CHANNEL =
  process.env.E2E_ECHO_CHANNEL ?? '#flightless';

/**
 * Message sent to the echo channel to nudge the bot into replying.
 * The bot just needs to see *any* message and respond; the exact text
 * doesn't matter as long as the bot doesn't filter it out.
 */
export const E2E_ECHO_TRIGGER_MESSAGE =
  process.env.E2E_ECHO_TRIGGER_MESSAGE ?? '!echo please give incoming message';

/**
 * Public key (64-char hex) of a nearby node that will ACK direct messages
 * sent by the test radio. This node must have the test radio's public key
 * in its contact list. Used only by the partner-radio DM ACK test.
 */
export const E2E_PARTNER_RADIO_PUBKEY =
  process.env.E2E_PARTNER_RADIO_PUBKEY ??
  'ae92577bae6c269a1da3c87b5333e1bdb007e372b66e94204b9f92a6b52a62b1';

/**
 * Display name for the partner radio node above. Used in UI assertions
 * (searching the sidebar, verifying the conversation header, etc.).
 */
export const E2E_PARTNER_RADIO_NAME =
  process.env.E2E_PARTNER_RADIO_NAME ?? 'FlightlessDt\u{1F95D}';

