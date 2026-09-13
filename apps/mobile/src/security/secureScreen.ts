/**
 * Screens that show what callers said are kept out of screenshots and the app switcher.
 *
 * Counted rather than toggled: a transcript opened over a summary opened over the list is three
 * secure screens, and closing the transcript must not unprotect the two still underneath.
 */
import { useEffect } from 'react';

import NativeSecureScreen, {
  type Spec,
} from '../calls/native/NativeSecureScreen';

export interface SecureWindow {
  hold(): () => void;
}

/** `native` is read each time it is needed, so a module that loads late is still found. */
export function secureWindow(
  native: () => Spec | null | undefined,
): SecureWindow {
  let holders = 0;
  return {
    hold: () => {
      holders += 1;
      if (holders === 1) {
        native()?.setSecure(true);
      }
      let released = false;
      return () => {
        if (released) {
          return;
        }
        released = true;
        holders -= 1;
        if (holders === 0) {
          native()?.setSecure(false);
        }
      };
    },
  };
}

const shared = secureWindow(() => NativeSecureScreen);

/** Keep this screen, while it is mounted, out of screenshots and the app switcher. */
export function useSecureScreen(window: SecureWindow = shared): void {
  useEffect(() => window.hold(), [window]);
}
