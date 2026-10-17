/* SPDX-License-Identifier: GPL-3.0-or-later
 * New GUI regression fixture for darling-rust; not an upstream application.
 */
#import <AppKit/AppKit.h>
#include <stdio.h>
#include <stdlib.h>
@interface Calculator : NSObject {
 NSWindow *window;
 NSTextField *display;
 NSButton *add;
 NSButton *multiply;
}
- (void)addTwo:(id)sender;
- (void)multiplyTwo:(id)sender;
- (void)verify:(NSTimer *)timer;
@end
@implementation Calculator
- (id)init {
 self=[super init];
 window=[[NSWindow alloc] initWithContentRect:NSMakeRect(100,100,440,210)
  styleMask:NSTitledWindowMask|NSClosableWindowMask|NSMiniaturizableWindowMask backing:NSBackingStoreBuffered defer:NO];
 [window setTitle:@"Darling Rust Calculator"];
 display=[[NSTextField alloc] initWithFrame:NSMakeRect(20,130,400,45)];
 [display setStringValue:@"19"];
 [[window contentView] addSubview:display];
 add=[[NSButton alloc] initWithFrame:NSMakeRect(20,60,190,45)];
 [add setTitle:@"Add 2"]; [add setTarget:self]; [add setAction:@selector(addTwo:)];
 [[window contentView] addSubview:add];
 multiply=[[NSButton alloc] initWithFrame:NSMakeRect(225,60,190,45)];
 [multiply setTitle:@"Multiply by 2"]; [multiply setTarget:self]; [multiply setAction:@selector(multiplyTwo:)];
 [[window contentView] addSubview:multiply];
 [window makeKeyAndOrderFront:nil];
 printf("CALCULATOR_WINDOW_CREATED\n"); fflush(stdout);
 const char *seconds=getenv("GUI_PROBE_SECONDS");
 [NSTimer scheduledTimerWithTimeInterval:seconds?atof(seconds):4.0 target:self selector:@selector(verify:) userInfo:nil repeats:NO];
 return self;
}
- (void)addTwo:(id)sender { [display setIntValue:[display intValue]+2]; }
- (void)multiplyTwo:(id)sender { [display setIntValue:[display intValue]*2]; }
- (void)verify:(NSTimer *)timer {
 [display setIntValue:19]; [add performClick:nil]; [multiply performClick:nil];
 BOOL success=[display intValue]==42 && [window isVisible];
 printf("CALCULATOR_SELFTEST %s value=%d visible=%d\n",success?"PASS":"FAIL",[display intValue],[window isVisible]); fflush(stdout);
 if (!success) exit(2);
 [NSApp terminate:nil];
}
@end
int main(void) {
 NSAutoreleasePool *pool=[NSAutoreleasePool new];
 [NSApplication sharedApplication];
 Calculator *calculator=[Calculator new];
 [NSApp run];
 [calculator release]; [pool drain]; return 0;
}
