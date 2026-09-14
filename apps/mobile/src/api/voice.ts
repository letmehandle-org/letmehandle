/** Readable names for the voice shapes in the generated schema. */
import type { components } from '@letmehandle/api-client';

type Schemas = components['schemas'];

export type VoiceCatalogue = Schemas['VoiceCatalogueResponse'];
export type Voice = Schemas['VoicePayload'];
export type VoiceCapabilities = Schemas['VoiceCapabilitiesPayload'];
export type VoiceSelection = Schemas['VoiceSelectionResponse'];
