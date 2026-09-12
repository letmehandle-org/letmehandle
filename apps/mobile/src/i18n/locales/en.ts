/**
 * British English, the only shipped locale (D-017).
 *
 * Keys are grouped by the screen or concept they belong to. A key used in two places belongs
 * under `common`: duplicating a string under two keys means one of them gets changed and the
 * other does not.
 */
export const en = {
  common: {
    appName: 'LetMeHandle',
    continue: 'Continue',
    back: 'Back',
    tryAgain: 'Try again',
    save: 'Save',
    add: 'Add',
    remove: 'Remove',
    done: 'Done',
    somethingWentWrong: 'Something went wrong. Please try again.',
    noConnection: 'Could not reach the service. Check your connection.',
  },
  welcome: {
    title: 'LetMeHandle',
    subtitle:
      'An assistant that answers your calls, and knows when to fetch you.',
    start: 'Get started',
  },
  phone: {
    title: 'What is your number?',
    subtitle: 'This is the number your assistant will answer for.',
    label: 'Phone number',
    placeholder: '+12025550143',
    invalid: 'Enter your number in full, including the country code.',
    rateLimited: 'Too many attempts. Try again in a little while.',
  },
  code: {
    title: 'Enter your code',
    subtitle: 'We sent a six-digit code to {{number}}.',
    label: 'Code',
    invalid: 'That code is not valid.',
    resend: 'Send another code',
  },
  profile: {
    title: 'Your profile',
    name: 'Name',
    namePlaceholder: 'What should your assistant call you?',
    number: 'Number',
    save: 'Save',
    saved: 'Saved',
    signOut: 'Sign out',
  },
  home: {
    title: 'LetMeHandle',
    subtitle: 'Nothing is handling your calls yet.',
    profile: 'Profile',
    settings: 'Settings',
  },
  onboarding: {
    progress: 'Step {{done}} of {{total}}',
    skip: 'Skip for now',
    loadFailed: 'Could not load your setup.',
    saveFailed: 'That could not be saved. Nothing has changed.',
  },
  settings: {
    title: 'Settings',
    subtitle: 'Everything your assistant goes on when it answers.',
    saved: 'Saved',
    saveFailed:
      'That could not be saved, so your previous setting has been put back.',
  },
  /**
   * The voice screen, which is not a preference section: it is its own resource on the API,
   * with its own list of what a deployment's provider offers.
   */
  voice: {
    title: 'The voice it answers in',
    subtitle: 'What everybody who calls you hears.',
    choose: 'Voice',
    default: 'Whichever your assistant picks',
    inUse: 'Calls are answered in {{voice}}.',
    notYourChoice:
      'Calls are being answered in {{voice}}, not the voice you chose, because that voice ' +
      'is not available right now.',
    withdrawn:
      'The voice you chose is no longer offered, so calls are being answered in {{voice}}. ' +
      'Choose another below.',
    loadFailed: 'Could not load the voices on offer.',
    saved: 'Saved',
    saveFailed:
      'That voice could not be set, so your previous one has been put back.',
  },
  /**
   * Keys under `preferences` are named for the API sections they edit, so a screen can look up
   * its own title from the section it was given rather than carrying a second mapping that has
   * to be kept in step with the first.
   */
  preferences: {
    introduction: {
      title: 'Let us set your assistant up',
      subtitle: 'Six short questions. You can change any of it later.',
      body:
        'Your assistant answers the calls you do not want to take, and fetches you for the ' +
        'ones you do. What it does depends entirely on what you say next.',
    },
    call_handling: {
      title: 'Calls you are not expecting',
      subtitle:
        'There is no safe guess here, so this is the one question to answer.',
      defaultPosture: 'Someone who is not in your contacts',
      anonymousPosture: 'Someone withholding their number',
      escalateAtOrAbove: 'Fetch you when a call is at least',
      categories: 'Particular kinds of call',
      categoryDefault: 'Same as everyone else',
      blocked: 'Never put through',
    },
    important_contacts: {
      title: 'People who always get through',
      subtitle:
        'Calls from these numbers are treated as you say, not as the assistant judges.',
      label: 'Name',
      labelPlaceholder: 'Who is this?',
      number: 'Number',
      numberPlaceholder: '+12025550143',
      posture: 'When they call',
      empty: 'Nobody yet.',
      invalidNumber: 'Enter the number in full, including the country code.',
      invalidLabel: 'Give this person a name so you can recognise the entry.',
      duplicate: 'That number is already on the list.',
    },
    hours: {
      title: 'Your hours',
      subtitle:
        'When you are working, and when you would rather not be disturbed.',
      working: 'Working hours',
      quiet: 'Quiet hours',
      start: 'From',
      end: 'Until',
      zone: 'Timezone',
      invalidTime: 'Use a 24-hour time, such as 09:00.',
      emptyWindow:
        'A window that starts and ends at the same time covers nothing.',
      invalidZone: 'Name the timezone, such as Europe/London.',
    },
    authority: {
      title: 'What it may do for you',
      subtitle: 'Nothing is on unless you say so.',
    },
    notifications: {
      title: 'When to tell you',
      subtitle:
        'Being fetched during a call is not optional; everything else is.',
      on_handled_call: 'A call was handled',
      on_blocked_call: 'A call was turned away',
      on_missed_escalation: 'It needed you and could not reach you',
      daily_summary: 'A summary once a day',
      respect_quiet_hours: 'Hold these until your quiet hours are over',
    },
    personality: {
      title: 'How it should sound',
      subtitle:
        'And the handful of things you care enough about to be interrupted for.',
      formality: 'Tone',
      verbosity: 'How much it says',
      topics: 'Things you care about',
      topicPlaceholder: 'the school run',
      topicsEmpty: 'Nothing yet.',
      invalidTopic: 'A topic is a short phrase, not a sentence.',
      duplicateTopic: 'That is already on the list.',
    },
    posture: {
      pass_through: 'Ring my phone',
      handle_with_agent: 'Let the assistant answer',
      reject: 'Turn it away',
    },
    category: {
      known_contact: 'Someone in your contacts',
      delivery: 'Deliveries',
      healthcare: 'Healthcare',
      education: 'Schools and childcare',
      financial: 'Banks and insurers',
      service_provider: 'Tradespeople and utilities',
      sales: 'Sales calls',
      spam: 'Suspected spam',
      unknown: 'Anything unrecognised',
    },
    importance: {
      ignorable: 'Anything at all',
      low: 'Worth a mention',
      routine: 'Routine',
      notable: 'Notable',
      urgent: 'Urgent only',
    },
    capability: {
      answer_questions_about_availability: 'Say when you are free',
      share_delivery_instructions: 'Give delivery instructions',
      confirm_appointments: 'Confirm an appointment',
      reschedule_appointments: 'Move an appointment',
      decline_on_the_users_behalf: 'Say no on your behalf',
      take_a_message: 'Take a message',
      share_contact_details: 'Share your contact details',
    },
    formality: {
      warm: 'Warm',
      neutral: 'Neutral',
      formal: 'Formal',
    },
    verbosity: {
      brief: 'Briefly',
      normal: 'Normally',
      detailed: 'In detail',
    },
  },
} as const;

export type Translations = typeof en;
