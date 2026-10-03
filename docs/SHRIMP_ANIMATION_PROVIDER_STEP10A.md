# Shrimp Animation Provider v0.1 — Step 10A

## Goal

Step 10A adds the first real-platform acceptance path for the Step 10
Controlled Publisher Execution engine.

The selected provider is **YouTube Data API v3**.

The acceptance is intentionally sacrificial and private-only:

```text
PUBLISH_AUTHORIZED
      ↓
YOUTUBE_CONTROLLED execution snapshot
      ↓
OAuth sacrificial-channel preflight
      ↓
videos.insert  (PRIVATE)
      ↓
provider read-back / reconcile
      ↓
videos.update  (final metadata, still PRIVATE)
      ↓
provider read-back / privacy verification
      ↓
videos.delete
      ↓
read-only deletion verification
      ↓
CLEANED_UP
```

No public or unlisted visibility is allowed by this acceptance path.

## Why YouTube first

YouTube provides documented OAuth-authorized methods for the complete acceptance
lifecycle:

- `videos.insert` — media upload;
- `videos.list` — provider read-back;
- `videos.update` — deterministic final metadata;
- `videos.delete` — cleanup.

Step 10A uses these operations without adding automatic write retries.

## Runtime gates

The live path requires all of the Step 10 gates plus:

```text
SHRIMP_YOUTUBE_LIVE_ACCEPTANCE_ENABLED=true
SHRIMP_YOUTUBE_LIVE_ACCEPTANCE_KEY=<independent secret>

SHRIMP_YOUTUBE_OAUTH_CLIENT_ID=<sacrificial OAuth app>
SHRIMP_YOUTUBE_OAUTH_CLIENT_SECRET=<secret>
SHRIMP_YOUTUBE_OAUTH_REFRESH_TOKEN=<sacrificial channel refresh token>
```

The OAuth grant must be created for the sacrificial channel with offline access
and the YouTube management scope:

```text
https://www.googleapis.com/auth/youtube.force-ssl
```

Only the refresh token is stored in the Preview secret store. Short-lived
access tokens are minted at runtime and are never committed to the repository.

The live-acceptance key must be independent from:

- human build approval key;
- human release key;
- deployment authorization key;
- Production execution key;
- Preview acceptance key;
- Step 8 human-review key;
- Step 9 publish-authorization key;
- Step 10 publisher-execution key.

## Sacrificial channel binding

The Step 9 Publish Target `account_reference` must be the exact YouTube
`UC...` channel ID.

Before any provider write, Step 10A calls the authenticated channel lookup and
requires:

```text
authenticated_channel_id == execution.account_reference
```

The account must also pass the Step 10 sacrificial account allowlist and must
not appear in the real/main account denylist.

## Category preflight

YouTube requires a category ID when updating the `snippet` part of a video.

Step 10A therefore validates the configured category before upload.

Default:

```text
SHRIMP_YOUTUBE_DEFAULT_CATEGORY_ID=22
```

A non-assignable or unavailable category fails closed before the first write.

## Upload phase

Step 10 `upload_write_count` remains the authoritative exactly-once logical
write budget.

The YouTube adapter performs one multipart `videos.insert` request containing:

- the frozen episode MP4;
- temporary Step 10A title;
- deterministic reconciliation marker;
- configured category;
- `privacyStatus=private`.

The acceptance media size is bounded by:

```text
SHRIMP_YOUTUBE_LIVE_ACCEPTANCE_MAX_MEDIA_BYTES
```

Default: 50 MiB.

## Reconciliation marker

Every sacrificial video description contains:

```text
[shrimp-step10a:<upload_idempotency_key>]
```

The marker is derived from the immutable Step 10 execution identity.

If the upload response is lost, Step 10A may inspect the authenticated channel's
recent upload playlist and locate the unique private video carrying that marker.

This recovery path is read-only.

If zero matches exist, the execution remains unresolved.
If multiple matches exist, reconciliation fails closed.

## Publish phase

Step 10 `publish_write_count` remains bounded to one.

The YouTube publish phase uses `videos.update` to replace temporary metadata
with the immutable Step 9 Publish Plan metadata.

The adapter always writes:

```text
status.privacyStatus = private
```

even if another visibility appears in upstream generic metadata.

The reconciliation marker remains in the description after the metadata update.

## Provider read-back

After publish, Step 10A reads the provider object and verifies:

- video ID exists;
- channel ID equals the sacrificial channel;
- reconciliation marker exists;
- expected final title is present;
- category is present;
- `privacyStatus == private`.

Only then does the acceptance run enter:

```text
VERIFIED_PRIVATE
```

## Cleanup

Cleanup has a separate provider-write budget:

```text
0 <= cleanup_write_count <= 1
```

The count is persisted as 1 before `videos.delete`.

If DELETE returns a transport timeout or an ambiguous 5xx outcome, a second
DELETE is forbidden.

The only legal recovery is read-only verification using `videos.list`.

Possible cleanup conclusions:

```text
ACCEPTED
RECONCILED_DELETED
RECONCILED_PRESENT
AMBIGUOUS
REJECTED
```

### Emergency cleanup

Once a provider video ID is known, any later verification failure also enters
the same one-shot cleanup path. Cleanup verifies exact sacrificial-channel
ownership plus the deterministic reconciliation marker, but deliberately does
not require the object to already be private. This prevents an unexpected
visibility drift from blocking deletion.

A successful acceptance must finish as:

```text
acceptance_status = CLEANED_UP
private_visibility_verified = true
provider_read_back_verified = true
cleanup_verified = true
production_account_touched = false
public_visibility_observed = false
```

## Audit table

Migration 043 adds:

```text
shrimp_animation_youtube_live_acceptance_runs
```

The table records:

- Step 10 execution ID;
- expected and authenticated channel IDs;
- reconciliation marker;
- provider video ID;
- provider video URL;
- privacy and processing states;
- upload/publish evidence;
- provider read-back evidence;
- cleanup write count and outcome;
- cleanup verification;
- failure evidence.

Acceptance identity, authenticated channel binding, video ID and provider URL
become immutable once recorded.

## API

Run/resume one controlled acceptance:

```text
POST
/v1/shrimp-animation/publish-executions/{execution_id}/youtube-live-acceptance

X-Shrimp-YouTube-Live-Acceptance-Key: <independent-key>
```

Read audit state:

```text
GET
/v1/shrimp-animation/publish-executions/{execution_id}/youtube-live-acceptance
```

A completed `CLEANED_UP` run is replay-safe and does not perform another
provider write.

## CI boundary

CI never receives Google OAuth credentials.

CI uses an injected YouTube fixture that exercises:

1. sacrificial channel preflight;
2. private upload;
3. final private publish;
4. provider read-back;
5. cleanup write;
6. deliberately lost cleanup response;
7. read-only cleanup reconciliation;
8. no second cleanup write;
9. audit immutability.

The final repository safety scan rejects:

- Production/main account access;
- public visibility;
- cleanup write count greater than one;
- a `CLEANED_UP` acceptance missing private/read-back/cleanup verification.

## Default safety state

Repository defaults remain:

```text
SHRIMP_YOUTUBE_LIVE_ACCEPTANCE_ENABLED=false
SHRIMP_PUBLISH_EXECUTOR_ENABLED=false
SHRIMP_PUBLISH_EXECUTION_ADAPTER=MOCK
```

Therefore merging or deploying Step 10A code alone cannot publish to YouTube.

A real acceptance requires explicit Preview-scoped secrets, explicit
sacrificial account/target allowlists, a Step 9 authorization decision, a
Step 10 execution snapshot, and the independent Step 10A acceptance key.
