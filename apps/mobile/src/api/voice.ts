/**
 * The voice shapes, named.
 *
 * The generated package names the shapes the earlier phases used and stops there, and it is
 * generated output that nothing here may edit. Naming these four in one place is what keeps
 * `components['schemas'][...]` out of the screens, where a change to the generator would
 * otherwise have to be followed into every file that reached through it.
 */
import type { components } from '@letmehandle/api-client';

type Schemas = components['schemas'];

export type VoiceCatalogue = Schemas['VoiceCatalogueResponse'];
export type Voice = Schemas['VoicePayload'];
export type VoiceCapabilities = Schemas['VoiceCapabilitiesPayload'];
export type VoiceSelection = Schemas['VoiceSelectionResponse'];
