/** Keeps screens with callers' words out of screenshots, counted so nested screens stay protected (D-035). */
import { useEffect } from 'react';

import NativeSecureScreen, {
  type Spec,
} from '../calls/native/NativeSecureScreen';

export interface SecureWindow {
  hold(): () => void;
}

/** Reads `native` each time, so a module that loads late is still found. */
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
