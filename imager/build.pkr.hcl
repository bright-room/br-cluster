build {
  sources = ["source.arm.ubuntu_rpi"]

  provisioner "file" {
    sources     = var.cloud_config_files
    destination = "/boot/firmware/"
  }
}
