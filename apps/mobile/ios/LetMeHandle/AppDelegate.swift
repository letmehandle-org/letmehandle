import UIKit
import React
import React_RCTAppDelegate
import ReactAppDependencyProvider

@main
class AppDelegate: UIResponder, UIApplicationDelegate {
  var window: UIWindow?

  var reactNativeDelegate: ReactNativeDelegate?
  /// Drawn over the app while it is not in front, so the app switcher's snapshot shows nothing.
  private var privacyCover: UIView?
  var reactNativeFactory: RCTReactNativeFactory?

  func application(
    _ application: UIApplication,
    didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
  ) -> Bool {
    let delegate = ReactNativeDelegate()
    let factory = RCTReactNativeFactory(delegate: delegate)
    delegate.dependencyProvider = RCTAppDependencyProvider()

    reactNativeDelegate = delegate
    reactNativeFactory = factory

    window = UIWindow(frame: UIScreen.main.bounds)

    factory.startReactNative(
      withModuleName: "LetMeHandle",
      in: window,
      launchOptions: launchOptions
    )

    return true
  }

  /// iOS takes the app switcher's snapshot as the app resigns active, and gives an app no
  /// per-screen way to refuse it or to refuse screenshots. The app is call summaries and what
  /// callers said, so the whole of it is covered rather than guessing which screen is showing.
  func applicationWillResignActive(_ application: UIApplication) {
    guard privacyCover == nil, let window else { return }
    let cover = UIView(frame: window.bounds)
    cover.backgroundColor = UIColor(red: 0xF8 / 255, green: 0xF6 / 255, blue: 1, alpha: 1)
    cover.autoresizingMask = [.flexibleWidth, .flexibleHeight]
    window.addSubview(cover)
    privacyCover = cover
  }

  func applicationDidBecomeActive(_ application: UIApplication) {
    privacyCover?.removeFromSuperview()
    privacyCover = nil
  }
}

class ReactNativeDelegate: RCTDefaultReactNativeFactoryDelegate {
  override func sourceURL(for bridge: RCTBridge) -> URL? {
    self.bundleURL()
  }

  override func bundleURL() -> URL? {
#if DEBUG
    RCTBundleURLProvider.sharedSettings().jsBundleURL(forBundleRoot: "index")
#else
    Bundle.main.url(forResource: "main", withExtension: "jsbundle")
#endif
  }
}
