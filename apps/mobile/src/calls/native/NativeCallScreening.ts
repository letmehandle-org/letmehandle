/** The native call screening module as codegen sees it: strings and promises, absent where no screening exists. */
import type { CodegenTypes, TurboModule } from 'react-native';
import { TurboModuleRegistry } from 'react-native';

export interface Spec extends TurboModule {
  /** `held`, `available` or `unavailable`: see `RoleStatus` in `../screeningRole.ts`. */
  roleStatus(): Promise<string>;
  /** Asks the user for the role. Resolves `held`, `declined` or `unavailable`. */
  requestRole(): Promise<string>;
  /** Stores the rules the screening service applies. Rejects a document it could not read. */
  writeRulesSnapshot(snapshot: string): Promise<void>;
  /** Records calls from now on, for the account that has signed in. Nothing is recorded before. */
  startRecordingCalls(): Promise<void>;
  /** Forgets the rules and every unreported call, and stops recording, for a sign-out. */
  forgetAccount(): Promise<void>;
  /** The unreported call events, as a JSON array of call reports. */
  pendingCallEvents(): Promise<string>;
  /** Forgets events the backend has stored. */
  acknowledgeCallEvents(eventIds: ReadonlyArray<string>): Promise<void>;
  /** Something new is waiting to be reported. */
  readonly onCallEventsPending: CodegenTypes.EventEmitter<void>;
}

export default TurboModuleRegistry.get<Spec>('CallScreening');
