#!/bin/zsh
# EA-Description: Example Extension Attribute — reports macOS version
# EA-DataType: STRING
#
# The filename stem ("Example EA") becomes the EA name in Jamf Pro.
# The parent folder (GENERAL) sets the inventoryDisplayType.
# EA-Description and EA-DataType are parsed from these header comments.

sw_vers -productVersion | xargs -I{} echo "<result>{}</result>"
