/** The network's or SIM's country as codegen sees it; Android only. */
import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  /** A two-letter country code, lower case, or null when there is no network or SIM. */
  networkCountry(): string | null;
}

export default TurboModuleRegistry.get<Spec>('DeviceCountry');
