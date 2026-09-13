/**
 * The country the phone's network says it is in, as the codegen sees it.
 *
 * Android only: the network's country, else the SIM's. iOS no longer tells an app its carrier's
 * country, so the module is absent there and the region setting is used instead.
 */
import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  /** A two-letter country code, lower case, or null when there is no network or SIM. */
  networkCountry(): string | null;
}

export default TurboModuleRegistry.get<Spec>('DeviceCountry');
