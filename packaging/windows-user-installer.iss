; Prototype only. Compile natively with Inno Setup 6 after licensing/native qualification.
#ifndef BundleRoot
  #error BundleRoot must name the verified one-directory bundle
#endif
#ifndef InstallerOutput
  #error InstallerOutput must name a private build output directory
#endif
[Setup]
AppId={{AAB1E53F-9EBF-4DF8-811F-4B2689B92065}
AppName=LMThermal Desktop
AppVersion=0.1.0-preview
DefaultDirName={localappdata}\Programs\LMThermal Desktop
DefaultGroupName=LMThermal Desktop
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#InstallerOutput}
OutputBaseFilename=LMThermal-Desktop-preview-setup
Compression=lzma2
SolidCompression=yes
UninstallDisplayIcon={app}\LMThermal-Desktop.exe
[Files]
Source: "{#BundleRoot}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
[Icons]
Name: "{group}\LMThermal Desktop"; Filename: "{app}\LMThermal-Desktop.exe"
; No file associations, drivers, scheduled tasks, firewall or machine-level writes.
; User recordings and diagnostic state are intentionally outside the install inventory.
