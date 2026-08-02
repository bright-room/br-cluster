# renovate: datasource=docker depName=ubuntu versioning=ubuntu
variable "ubuntu_version" {
  type    = string
  default = "26.04"
}

variable "hostname" {
  type = string
}

variable "cloud_config_files" {
  type    = list(string)
  default = []
}
