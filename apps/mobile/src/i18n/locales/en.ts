/**
 * British English, the only shipped locale (D-017).
 *
 * Keys are grouped by the screen or concept they belong to. A key used in two places belongs
 * under `common`: duplicating a string under two keys means one of them gets changed and the
 * other does not.
 *
 * The design asks for as few words as a screen can carry. Where an icon or a drawing says it,
 * there is no string here for it to repeat.
 */
export const en = {
  common: {
    appName: 'LetMeHandle',
    continue: 'Continue',
    next: 'Next',
    back: 'Back',
    tryAgain: 'Try again',
    save: 'Save',
    add: 'Add',
    remove: 'Remove {{item}}',
    done: 'Done',
    somethingWentWrong: 'Something went wrong. Please try again.',
    noConnection: 'Could not reach the service. Check your connection.',
    saveFailed: "Couldn't save. It's been put back the way it was.",
  },
  welcome: {
    title: 'Your phone,',
    titleAccent: 'handled.',
    start: 'Get started',
    features: {
      answers: 'Answers your calls',
      rings: 'Rings you if needed',
      private: 'Never records',
    },
    illustration: {
      delivery: 'Delivery',
      needsYou: 'Needs you',
      spam: 'Spam',
      described:
        'A phone with its calls being looked after: a delivery settled, spam turned away, and one call that needs you.',
    },
  },
  phone: {
    title: 'Your number',
    subtitle: 'The one your assistant will answer for.',
    label: 'Phone number',
    placeholder: '+12025550143',
    invalid: 'Enter your number in full, including the country code.',
    rateLimited: 'Too many attempts. Try again in a little while.',
  },
  code: {
    title: 'Enter the code',
    subtitle: 'Sent to {{number}}',
    label: 'Code',
    invalid: 'That code is not valid.',
    resend: 'Send another code',
    // FOR TESTING ONLY — remove before launch, with showsTestingCode.
    testingHint: 'Testing build: the code is 123456.',
  },
  setup: {
    progress: 'Step {{done}} of {{total}}',
    skip: 'Skip for now',
    loadFailed: 'Could not load your setup.',
    saveFailed: "That couldn't be saved. Nothing has changed.",
    who: { title: 'How your calls work' },
    when: { title: "When you're called" },
    hours: { title: 'When should it work?' },
    authority: { title: 'What may it do?' },
    done: {
      title: "You're all set",
      voice: 'Voice: {{voice}}',
      home: 'Go to Home',
    },
  },
  /** The two rules every call is sorted by, drawn as lanes. */
  lanes: {
    contacts: 'Your contacts',
    ringYou: 'Ring you',
    unknown: 'Unknown numbers',
    assistant: 'Your assistant',
    spam: 'Known spam is turned away',
    differs:
      'Your calls are set up differently from this. Use these two rules instead?',
    apply: 'Use these rules',
  },
  calls: {
    graph: {
      answers: 'Assistant answers',
      cantResolve: "It can't\nresolve it",
      urgent: "It's\nurgent",
      rings: 'Your phone rings',
      otherwise: 'Anything else waits in Activity',
      described:
        "Your assistant answers. If it can't resolve the call, or the call is urgent, your phone rings. Anything else waits in Activity.",
    },
    everyCall: 'Tell me about every call',
    evening: 'Evening round-up',
  },
  hours: {
    title: 'Hours',
    allTheTime: 'All the time',
    set: 'Set hours',
    always: '24/7',
    alwaysOn: 'Always on',
    figure_one: '{{hours}} hr',
    figure_other: '{{hours}} hrs',
    aDay: 'A day',
    from: 'From',
    to: 'To',
    outside: 'Outside these hours, calls ring you',
  },
  tabs: {
    home: 'Home',
    activity: 'Activity',
    settings: 'Settings',
  },
  home: {
    notYet: 'Not answering calls yet',
    callsToday: 'Calls today',
    whenItDoes: 'When it does',
    fillsRing: 'Settled calls fill this ring',
    ringsYou: "You're rung only if needed",
  },
  activity: {
    title: 'Activity',
    empty: 'Calls will appear here as they happen.',
  },
  settings: {
    title: 'Settings',
    calls: 'Calls',
    assistant: 'Your assistant',
    you: 'You',
    who: 'Who gets through',
    whoValue: 'Contacts',
    whoCustom: 'Custom',
    when: "When you're called",
    whenValue: 'If needed',
    hours: 'Hours',
    authority: 'What it may do',
    authorityValue: '{{granted}} of {{total}}',
    say: 'What it may say',
    sayNothing: 'Nothing',
    personalise: 'Personalise',
    account: 'Account',
  },
  /**
   * Call screening on this handset: what it does, what it cannot, and what refusing it means.
   * Only shown where the handset has a screening service to grant.
   */
  screening: {
    title: 'Screen calls before they ring',
    subtitle: 'Your rules, applied on this phone.',
    what:
      'With the call screening role, Android asks LetMeHandle about each incoming call from a ' +
      'number that is not in your contacts, before the phone rings. It applies your rules on the ' +
      'phone itself: it lets the call ring, rings silently during your quiet hours, or refuses it.',
    whatNot:
      'It never hears a call, never answers one, and cannot hand one to your assistant. Calls ' +
      'you asked the assistant to handle ring as normal on this phone. Android does not show it ' +
      'callers in your contacts or callers who hide their number, so those always ring.',
    held: 'Call screening is on.',
    offExplained:
      'Call screening is off. Every call rings exactly as it would without LetMeHandle.',
    declined:
      'You said no, so call screening is off and every call rings as it would without ' +
      'LetMeHandle. You can turn it on here whenever you like.',
    notAskedAgain:
      'If nothing appears when you tap below, Android has stopped asking. You can choose ' +
      'LetMeHandle as the caller ID and spam app in your phone’s default apps settings.',
    unavailable:
      'This phone’s version of Android does not let apps screen calls, so every call rings as ' +
      'it would without LetMeHandle.',
    failed: 'Could not tell whether call screening is on.',
    turnOn: 'Turn on call screening',
    rulesNotSaved:
      'Your latest rules could not be applied on this phone, so calls ring as normal until they can.',
    activityTitle: 'Call activity',
    activityWhat:
      'Lets LetMeHandle see that a call was answered or ended, so it can appear in your history. ' +
      'It does not see who called and does not hear anything.',
    activityGranted: 'Call activity is on.',
    activityOff:
      'Call activity is off. Screening still works; answered and ended calls are not recorded.',
    activityTurnOn: 'Allow call activity',
    activityRationaleTitle: 'See when calls are answered and end',
    activityRationale:
      'LetMeHandle records that a call was answered or ended. It does not see who called and ' +
      'does not hear the call.',
    activityRationaleAccept: 'Continue',
    settingsRow: 'Call screening',
  },
  personalise: {
    title: 'Personalise',
    voice: 'Voice',
    manner: 'Manner',
    length: 'Length',
    topics: 'Topics',
    voiceUnavailable: 'Could not load the voices on offer.',
    voiceSaveFailed:
      "That voice couldn't be set, so your previous one is still in use.",
    voiceNotYourChoice:
      "Calls are answered in {{voice}} because the voice you chose isn't available right now.",
  },
  topics: {
    title: 'Topics',
    add: 'Add a topic',
    placeholder: 'the school run',
    count: '{{count}} of {{max}}',
    empty: 'Nothing yet.',
  },
  say: {
    title: 'What it may say',
    never: 'Nothing else is ever shared.',
    allowed: 'It may say',
    add: 'Add something',
    placeholder: "He's usually free after six",
    empty: 'Nothing yet.',
    invalid: 'Keep it to one short sentence.',
    duplicate: 'That is already on the list.',
    full: 'That is as many as it can hold.',
  },
  account: {
    title: 'Account',
    name: 'Your name',
    namePlaceholder: 'What should your assistant call you?',
    number: 'Number',
    saved: 'Saved',
    signOut: 'Sign out of this phone',
    otherDevices: 'Your other devices stay signed in.',
  },
  /** The voice's own resource on the API, with its own list of what a provider offers. */
  voice: {
    default: 'Whichever your assistant picks',
  },
  /**
   * Keys under `preferences` are named for the API sections they edit, and validation messages
   * are looked up here by the code that raises them.
   */
  preferences: {
    important_contacts: {
      invalidNumber: 'Enter the number in full, including the country code.',
      invalidLabel: 'Give this person a name so you can recognise the entry.',
      duplicate: 'That number is already on the list.',
    },
    hours: {
      invalidTime: 'Use a 24-hour time, such as 09:00.',
      emptyWindow:
        'A window that starts and ends at the same time covers nothing.',
      invalidZone: 'Name the timezone, such as Europe/London.',
    },
    personality: {
      invalidTopic: 'A topic is a short phrase, not a sentence.',
      duplicateTopic: 'That is already on the list.',
    },
    capability: {
      answer_questions_about_availability: "Say if you're free",
      share_delivery_instructions: 'Give delivery instructions',
      confirm_appointments: 'Confirm appointments',
      reschedule_appointments: 'Move appointments',
      decline_on_the_users_behalf: 'Decline for you',
      take_a_message: 'Take a message',
      share_contact_details: 'Share your contact details',
    },
    formality: {
      warm: 'Warm',
      neutral: 'Neutral',
      formal: 'Formal',
    },
    verbosity: {
      brief: 'Brief',
      normal: 'Normal',
      detailed: 'Detailed',
    },
  },
} as const;

export type Translations = typeof en;
