/**
 * Keeping what is on screen out of screenshots and the app switcher, as the codegen sees it.
 *
 * Android only: the window's secure flag. iOS has no per-screen equivalent an app may set, so it
 * covers the whole app when it leaves the foreground instead, natively, with nothing to call —
 * and this module is absent there. Nothing asks which platform it is on (D-005).
 */
import type { TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  /** Whether the window may be captured: false blocks screenshots and blanks the switcher. */
  setSecure(secure: boolean): void;
}

export default TurboModuleRegistry.get<Spec>('SecureScreen');
