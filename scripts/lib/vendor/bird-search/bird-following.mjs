#!/usr/bin/env node
/**
 * bird-following.mjs — Fetch the Following list for an X handle.
 *
 * Uses the same auth infrastructure as bird-search.mjs (AUTH_TOKEN + CT0).
 * Resolves the handle to a user ID, then paginates the Following GraphQL
 * endpoint via Bottom cursor entries.
 *
 * Usage:
 *   node bird-following.mjs <handle>
 *
 * Outputs JSON on stdout:
 *   { handles: ["..."], count: N }
 * or on failure:
 *   { error: "..." }
 */

import { TwitterClientBase } from './lib/twitter-client-base.js';
import { buildFollowingFeatures } from './lib/twitter-client-features.js';

const USER_BY_SCREEN_NAME_QUERY_ID = 'sLVLhk0bGj3MVFEKTdax1w';

const USER_FEATURES = {
  hidden_profile_likes_enabled: true,
  hidden_profile_subscriptions_enabled: true,
  rweb_tipjar_consumption_enabled: true,
  responsive_web_graphql_exclude_directive_enabled: true,
  verified_phone_label_enabled: false,
  subscriptions_verification_info_is_identity_verified_enabled: true,
  subscriptions_verification_info_verified_since_enabled: true,
  highlights_tweets_tab_ui_enabled: true,
  responsive_web_twitter_article_notes_tab_enabled: true,
  subscriptions_feature_can_gift_premium: false,
  creator_subscriptions_tweet_preview_api_enabled: true,
  responsive_web_graphql_skip_user_profile_image_extensions_enabled: false,
  responsive_web_graphql_timeline_navigation_enabled: true,
};

const MAX_PAGES = 50;
const PAGE_SIZE = 100;

class FollowingClient extends TwitterClientBase {
  async resolveUserId(screenName) {
    const variables = {
      screen_name: screenName,
      withSafetyModeUserFields: true,
    };
    const params = new URLSearchParams({
      variables: JSON.stringify(variables),
      features: JSON.stringify(USER_FEATURES),
    });

    const url = `https://x.com/i/api/graphql/${USER_BY_SCREEN_NAME_QUERY_ID}/UserByScreenName?${params}`;
    try {
      const resp = await this.fetchWithTimeout(url, {
        method: 'GET',
        headers: this.getHeaders(),
      });
      if (!resp.ok) {
        return { success: false, error: `HTTP ${resp.status}` };
      }
      const data = await resp.json();
      const userId = data?.data?.user?.result?.rest_id;
      if (!userId) {
        return { success: false, error: 'User not found' };
      }
      return { success: true, userId };
    } catch (e) {
      return { success: false, error: String(e) };
    }
  }

  async fetchFollowingPage(userId, cursor) {
    const queryId = (await this.getQueryId('Following')) || 'mWYeougg_ocJS2Vr1Vt28w';
    const variables = {
      userId,
      count: PAGE_SIZE,
      includePromotedContent: false,
    };
    if (cursor) variables.cursor = cursor;

    const params = new URLSearchParams({
      variables: JSON.stringify(variables),
      features: JSON.stringify(buildFollowingFeatures()),
    });

    const url = `https://x.com/i/api/graphql/${queryId}/Following?${params}`;
    const resp = await this.fetchWithTimeout(url, {
      method: 'GET',
      headers: this.getHeaders(),
    });
    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
    }
    return resp.json();
  }

  extractEntries(data) {
    const instructions =
      data?.data?.user?.result?.timeline?.timeline?.instructions || [];
    const entries = [];
    for (const instr of instructions) {
      if (instr?.type === 'TimelineAddEntries' && Array.isArray(instr.entries)) {
        entries.push(...instr.entries);
      }
    }
    return entries;
  }

  parsePage(data) {
    const entries = this.extractEntries(data);
    const handles = [];
    let bottomCursor = null;

    for (const entry of entries) {
      const entryId = entry?.entryId || '';
      const content = entry?.content || {};

      if (entryId.startsWith('cursor-bottom-') || content?.cursorType === 'Bottom') {
        bottomCursor = content?.value || bottomCursor;
        continue;
      }
      if (entryId.startsWith('cursor-top-') || content?.cursorType === 'Top') {
        continue;
      }

      const userResult =
        content?.itemContent?.user_results?.result || content?.user_results?.result;
      if (!userResult) continue;

      const handle =
        userResult?.core?.screen_name ||
        userResult?.legacy?.screen_name ||
        '';
      if (handle) handles.push(handle.toLowerCase());
    }

    return { handles, bottomCursor };
  }

  async fetchAllFollowing(userId) {
    const all = new Set();
    let cursor = null;
    const seenCursors = new Set();

    for (let page = 0; page < MAX_PAGES; page++) {
      const data = await this.fetchFollowingPage(userId, cursor);
      const { handles, bottomCursor } = this.parsePage(data);

      const before = all.size;
      for (const h of handles) all.add(h);
      const added = all.size - before;

      process.stderr.write(
        `  page ${page + 1}: +${added} handles (total ${all.size})\n`
      );

      if (!bottomCursor) break;
      if (seenCursors.has(bottomCursor)) break;
      if (handles.length === 0) break;
      seenCursors.add(bottomCursor);
      cursor = bottomCursor;
    }

    return Array.from(all);
  }
}

const args = process.argv.slice(2);
if (!args.length || args[0].startsWith('-')) {
  process.stderr.write('Usage: node bird-following.mjs <handle>\n');
  process.exit(1);
}

const handle = args[0].replace(/^@/, '');

const cookies = { authToken: process.env.AUTH_TOKEN, ct0: process.env.CT0 };
if (!cookies.authToken || !cookies.ct0) {
  process.stdout.write(JSON.stringify({ error: 'AUTH_TOKEN and CT0 env vars required' }));
  process.exit(1);
}

const client = new FollowingClient({ cookies, timeoutMs: 30000 });

try {
  const resolved = await client.resolveUserId(handle);
  if (!resolved.success) {
    process.stdout.write(JSON.stringify({ error: `resolve failed: ${resolved.error}` }));
    process.exit(1);
  }

  process.stderr.write(`  resolved @${handle} → ${resolved.userId}\n`);

  const handles = await client.fetchAllFollowing(resolved.userId);
  process.stdout.write(JSON.stringify({ handles, count: handles.length }));
  process.exit(0);
} catch (e) {
  process.stdout.write(JSON.stringify({ error: String(e) }));
  process.exit(1);
}
