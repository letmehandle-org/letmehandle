/** The window's secure flag as codegen sees it; Android only (D-005, D-035). */
import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  /** Whether the window may be captured: false blocks screenshots and blanks the switcher. */
  setSecure(secure: boolean): void;
}

export default TurboModuleRegistry.get<Spec>('SecureScreen');
