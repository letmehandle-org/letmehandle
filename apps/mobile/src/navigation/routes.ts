/** The typed route names and parameters of the three stacks. */
export type AuthStackParamList = {
  Welcome: undefined;
  PhoneNumber: undefined;
  VerifyCode: {
    challengeId: string;
    phoneNumber: string;
    /** Seconds until another code may be asked for, as the server said when this one was sent. */
    resendAfterSeconds: number;
  };
};

export type OnboardingStackParamList = {
  Step: undefined;
};

export type AppStackParamList = {
  Tabs: undefined;
  Who: undefined;
  When: undefined;
  Hours: undefined;
  Authority: undefined;
  Say: undefined;
  Personalise: undefined;
  Topics: undefined;
  Account: undefined;
  Privacy: undefined;
  CallScreening: undefined;
  Call: { callId: string };
  Transcript: { callId: string };
  Escalation: { callId: string };
};

export const AUTH_ROUTES = {
  welcome: 'Welcome',
  phoneNumber: 'PhoneNumber',
  verifyCode: 'VerifyCode',
} as const satisfies Record<string, keyof AuthStackParamList>;

export const ONBOARDING_ROUTES = {
  step: 'Step',
} as const satisfies Record<string, keyof OnboardingStackParamList>;

export const APP_ROUTES = {
  tabs: 'Tabs',
  who: 'Who',
  when: 'When',
  hours: 'Hours',
  authority: 'Authority',
  say: 'Say',
  personalise: 'Personalise',
  topics: 'Topics',
  account: 'Account',
  privacy: 'Privacy',
  callScreening: 'CallScreening',
  call: 'Call',
  transcript: 'Transcript',
  escalation: 'Escalation',
} as const satisfies Record<string, keyof AppStackParamList>;
