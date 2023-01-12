# PLEASE READ!

Honestly I didn't want to write this because of the brick risks involved. You have to edit many system files which when done wrong can lead to devastating consequences.

## Notes
- You lose eshop access.
- If you do not have a gamepad from the region you are changing to, after region changing install [Aroma](https://aroma.foryour.cafe) and use its region-free gamepad paring plugin.
- If you are not that experienced with Wii U Homebrew, do not follow this. It's not as easy as region changing, say the 3DS.
- Unblock updates with UFDiine before following this guide (if you dont re-create the update folder it will bite you in the ass later in the guide).
- That being said, be careful and have a NAND Backup just in case. I am not responsible if you fuck up your system.

## What you need:
- A Brain, a large one at that.
- A way to run [UDPIH](https://github.com/GaryOderNichts/udpih) to be able to run the recovery_menu. You *might* be able to do this without it but dont take your chances.
- A modified [recovery_menu](https://raw.githubusercontent.com/Lazr1026/regionchange/main/files/recovery_menu). ([Source](https://github.com/Lazr1026/recovery_menu))
- [Python](https://www.python.org/downloads/)
- [wupclient.py](https://raw.githubusercontent.com/Elpunical/mocha/master/ios_mcp/wupclient.py) (right-click -> Save link as… -> Click Save)
- A text editor. Notepad will be fine
- If you need help, join [my server](https://discord.gg/HNDcTEkcR3) and ask in `#help`.

## Region Changing
1. Create a file on the root of your sd named `network.cfg` and insert the following into it:
	- If using a wifi connection (obviously replace `ssid` and `key` with what they should be):
```
type=wifi
ssid=ssidhere
key=wifikeyhere
key_type=WPA2_PSK_AES
```
	- If using an ethernet connection:
```
type=eth
```
1. Format your console. Yes this is required. I'll explain why later (scroll down).
1. Pair a gamepad and then shut down.
1. Load the recovery_menu with UDPIH.
1. Navigate to "Load Network Configuration" and press a button to exit back to the main menu.
1. Start wupserver in the recovery_menu.
1. Open the command line/terminal where you saved `wupclient.py`
1. Windows: `py -3 -i wupclient.py` macOS/Linux: `python3 -i wupclient.py`
1. Input your IP when asked,
1. Insert`w.dl("/vol/system/config/sys_prod.xml")` into the CLI.
1. Open `sys_prod.xml` in a text editor.
	- Replace the `product_area` value with the desired region. 1 = JPN, 2 = USA, 4 = EUR. Also replace the `game_region` value with `119` (RegionHax).
	- Make sure you save your changes!
1. Insert `w.up("sys_prod.xml", "/vol/system/config/sys_prod.xml")` into the CLI
1. Exit wupclient with `exit()`
1. Press a button on the wiiu to shut down wupserver and go to `Set Coldboot Title`.
1. Set the System Settings for your region as the default title.
1. Reboot.
1. Set an internet connection.
1. Start a System Update. It should start to "Update".
1. Wait for the update to finish. When its done you should be taken to the desired regions Wii U Menu.
1. Complete initial setup.
- You will see duplicate titles. This is normal as the titles from the original region are still installed.

### WTF? WHY DO I HAVE TO FORMAT?
You have to format the system or else you will not boot after changing the `product_area` value. I dont know why especially since I have changed it in the past and I was still able to boot fine.

## Removing old titles
!> VERY easy way to brick if you arent paying attention.
1. Start FTP (one that lets you access system files).
1. Navigate to `storage_mlc01/sys/title/`
1. Click to view a list of titles to delete.
- [JPN](/docs/JPN.md)
- [USA](/docs/USA.md)
- [EUR](/docs/EUR.md)

## Credits
- Lazr - Figured out how to do it. The writeup is [here](/docs/writeup.md).
- GaryOderNichts - UDPIH and the recovery_menu. (kinda) urged me to make this.
- NoahAbc12345 - Helping figure out it out. In fact, he was writing [The Downgrade of Doom](https://noahabc12345.github.io/posts/the-downgrade-of-doom) while I wrote this if you want to check it out.  
(You could call us "The Bad Time Trio" /s)
