/**
 * Whether a voice can be listened to before it is chosen.
 *
 * Two halves have to agree, and this is the one place that asks both. The provider declares
 * whether it has samples at all; the generated client decides whether there is a route to
 * fetch one with. A deployment whose provider says yes and whose client has no route can no
 * more play a sample than one whose provider says no.
 *
 * The second half is checked against the generated schema rather than asserted, because the
 * failure it prevents is the tempting one: a play button wired to a hand-rolled fetch for a
 * path the backend never registered, which looks like a working control and is a 404. No such
 * fetch is written here, and none should be — when a provider with samples ships, the schema
 * gains the route, `SCHEMA_HAS_PREVIEW` stops compiling, and the control gets written then,
 * against something that exists.
 */
import type { paths } from '@letmehandle/api-client';

type SchemaHasPreview = '/v1/voices/{voice_id}/preview' extends keyof paths
  ? true
  : false;

// The annotation is the trip-wire; the value is today's answer.
const SCHEMA_HAS_PREVIEW: SchemaHasPreview = false;

/** Whether this build can play a sample of the voices this provider offers. */
export function canPreviewVoices(capabilities: {
  readonly preview: boolean;
}): boolean {
  // Widened on purpose: the caller is asking a question, not reading a literal that the
  // compiler would happily fold away along with the control hanging off it.
  const clientCanPreview: boolean = SCHEMA_HAS_PREVIEW;
  return capabilities.preview && clientCanPreview;
}
